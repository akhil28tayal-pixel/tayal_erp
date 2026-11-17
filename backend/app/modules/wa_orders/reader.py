"""Reads a dealer's WhatsApp message ("Sharma ji 200 bag ambuja plus kal bhejna") into an order: which dealer,
which materials and how many, when, which site.

With an Anthropic API key (ERP_ANTHROPIC_API_KEY) Claude reads it: it understands Hinglish, typos and voice-typed
text. It is given only this company's sellable products and a short list of likely dealers, and must pick from
those lists (it cannot invent ids). Without a key a simple built-in reader matches names and numbers."""

import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.collections.service import normalize_mobile
from app.modules.masters.models import CompanyProduct, Dealer, DealerContact, Product

IST = ZoneInfo("Asia/Kolkata")
STOP = {"ji", "bhai", "sir", "bag", "bags", "bori", "katta", "ton", "tons", "mt", "kg", "pcs", "ka", "ki", "ke", "ko",
        "se", "me", "hai", "and", "aur", "please", "pls", "plz", "send", "bhejo", "bhejna", "bhej", "dena", "de", "do",
        "kal", "aaj", "parso", "order", "chahiye", "site", "par", "pe", "the", "for", "maal", "gaadi", "truck", "cement",
        "sariya", "saria", "tmt", "rate", "wala", "wali", "wale", "abhi", "jaldi", "urgent", "subah", "shaam"}


class ReaderError(Exception):
    pass


# ------------------------------------------------------------------ what the reader may choose from
def products_for(db: Session, company_id: uuid.UUID) -> list[dict]:
    rows = db.execute(
        select(Product, CompanyProduct).join(CompanyProduct, CompanyProduct.product_id == Product.id)
        .where(CompanyProduct.company_id == company_id, CompanyProduct.is_active.is_(True),
               CompanyProduct.erp_enabled.is_(True), CompanyProduct.tally_deleted.is_(False),
               Product.is_active.is_(True))
    ).all()
    out = []
    for p, cp in rows:
        units = [u.uom for u in p.units if u.is_sellable] or [p.base_uom]
        default = next((u.uom for u in p.units if u.is_default_entry), units[0])
        out.append({"id": str(p.id), "name": p.name, "code": p.code, "brand": p.brand.name if p.brand else None,
                    "category": p.category, "tally_name": cp.tally_stock_item_name, "units": units,
                    "default_unit": default})
    return sorted(out, key=lambda x: x["name"])


def dealer_by_phone(db: Session, company_id: uuid.UUID, phone: str | None) -> Dealer | None:
    num = normalize_mobile(phone) if phone else None
    if not num:
        return None
    last10 = num[-10:]
    d = db.execute(select(Dealer).where(Dealer.company_id == company_id,
                                        func.regexp_replace(Dealer.tally_mobile, r"\D", "", "g").like(f"%{last10}"))
                   .limit(1)).scalar_one_or_none()
    if d is None:
        d = db.execute(select(Dealer).join(DealerContact, DealerContact.dealer_id == Dealer.id)
                       .where(Dealer.company_id == company_id,
                              func.regexp_replace(DealerContact.phone, r"\D", "", "g").like(f"%{last10}"))
                       .limit(1)).scalar_one_or_none()
    return d


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z]{3,}", text.lower()) if w not in STOP]


def dealer_candidates(db: Session, company_id: uuid.UUID, text: str, limit: int = 25, only=None) -> list[Dealer]:
    """Dealers whose name (or Tally name / alias) shares a word with the message, best matches first."""
    words = _words(text)
    if not words:
        return []
    conds = []
    for w in words[:12]:
        like = f"%{w}%"
        conds += [Dealer.name.ilike(like), Dealer.tally_ledger_name.ilike(like),
                  func.array_to_string(Dealer.aliases, " ").ilike(like)]
    q = select(Dealer).where(Dealer.company_id == company_id, Dealer.status == "ACTIVE", or_(*conds))
    if only is not None:
        q = q.where(Dealer.id.in_(only))
    rows = db.execute(q.limit(200)).scalars().all()

    def score(d: Dealer) -> float:
        name = (d.name or "").lower()
        hits = sum(1 for w in words if w in name or w in (d.tally_ledger_name or "").lower())
        return hits + SequenceMatcher(None, " ".join(words), name).ratio()

    return sorted(rows, key=score, reverse=True)[:limit]


# ------------------------------------------------------------------ Claude
TOOL = {
    "name": "record_order",
    "description": "Record what the dealer's WhatsApp message asks for.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_order": {"type": "boolean", "description": "false if the message is not asking for material "
                                                            "(greeting, payment info, complaint, question…)"},
            "dealer_id": {"type": ["string", "null"], "description": "id from DEALERS, or null if unsure"},
            "dealer_hint": {"type": ["string", "null"], "description": "the name/words in the message that refer "
                                                                       "to the buyer, as written"},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": ["string", "null"], "description": "id from PRODUCTS, null if unsure"},
                        "said": {"type": "string", "description": "the words for this item, as written"},
                        "qty": {"type": "number"},
                        "unit": {"type": "string", "description": "one of the product's units"},
                        "rate": {"type": ["number", "null"], "description": "price per unit only if the message "
                                                                            "states one"},
                    },
                    "required": ["said", "qty", "unit"],
                },
            },
            "delivery_date": {"type": ["string", "null"], "description": "YYYY-MM-DD if a day is asked for "
                                                                          "(kal = tomorrow, parso = day after)"},
            "urgent": {"type": "boolean"},
            "site": {"type": ["string", "null"], "description": "site / customer / place to deliver, if given"},
            "notes": {"type": ["string", "null"], "description": "anything else the office should know, in English"},
            "unclear": {"type": "array", "items": {"type": "string"},
                        "description": "short English notes on anything you could not work out"},
        },
        "required": ["is_order", "lines", "urgent", "unclear"],
    },
}

SYSTEM = """You read WhatsApp messages sent to a cement and TMT steel distributor in Delhi (India) and turn them
into an order. Messages are often in Hinglish (Hindi written in English letters), Hindi, or short English, with
typos and voice-typing mistakes. Common words: bag/bori/katta = bags of cement; sariya/saria = TMT steel bars;
ton/MT = tonnes; kal = tomorrow; parso = day after tomorrow; aaj = today; bhejo/bhejna/chahiye = send/need;
gaadi = truck; "ji"/"bhai" are polite words, not part of a name. "8mm/10mm/12mm…" are TMT bar sizes.
Brand words map to products: e.g. ambuja, acc, ultratech/ultra, shree, jk, wonder, birla; TMT brand codes
KD=Kamdhenu, JD=Jindal, BL=Birla, R=Rungta, RT=Rathi, SS=Shyam Steel.

Rules:
- Only use product_id values from PRODUCTS and dealer_id values from DEALERS. Never invent ids.
- If two products fit equally (e.g. "ambuja" when there are several Ambuja products), choose the most common
  plain one only if it is clearly the usual meaning; otherwise leave product_id null and say why in unclear.
- unit must be one of that product's units (BAG for cement bags, MT for tonnes, KG, PCS…). "2 gaadi" or
  "1 truck" is not a quantity you know: leave it as said, qty 0, and add a note to unclear.
- If the sender is already known (SENDER_DEALER is given) use that dealer unless the message clearly orders
  for a different listed dealer.
- Always call record_order exactly once."""


def _ai(text: str, sender: Dealer | None, candidates: list[Dealer], products: list[dict], today: date) -> dict:
    s = get_settings()
    dealers = [{"id": str(d.id), "name": d.name, **({"tally_name": d.tally_ledger_name}
               if d.tally_ledger_name and d.tally_ledger_name != d.name else {})} for d in candidates]
    if sender and str(sender.id) not in {x["id"] for x in dealers}:
        dealers.insert(0, {"id": str(sender.id), "name": sender.name})
    prompt = (f"TODAY: {today.isoformat()} ({today.strftime('%A')})\n"
              f"SENDER_DEALER: {json.dumps({'id': str(sender.id), 'name': sender.name}) if sender else 'unknown'}\n"
              f"DEALERS: {json.dumps(dealers, ensure_ascii=False)}\n"
              f"PRODUCTS: {json.dumps(products, ensure_ascii=False)}\n\n"
              f"MESSAGE:\n<<<\n{text.strip()[:2000]}\n>>>")
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": s.anthropic_model, "max_tokens": 1500, "system": SYSTEM, "tools": [TOOL],
                  "tool_choice": {"type": "tool", "name": "record_order"},
                  "messages": [{"role": "user", "content": prompt}]})
    except httpx.HTTPError as e:
        raise ReaderError(f"Could not reach the AI service: {e}") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise ReaderError(f"The AI service refused ({r.status_code}): {msg}")
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input") or {}
    raise ReaderError("The AI gave no answer")


# ------------------------------------------------------------------ built-in reader (no API key)
UNIT_WORDS = {"bag": "BAG", "bags": "BAG", "bori": "BAG", "katta": "BAG", "kattay": "BAG", "ton": "MT", "tons": "MT",
              "tonne": "MT", "mt": "MT", "kg": "KG", "pcs": "PCS", "piece": "PCS", "pieces": "PCS"}


def _rules(text: str, sender: Dealer | None, candidates: list[Dealer], products: list[dict], today: date) -> dict:
    low = text.lower()
    lines = []
    # "200 bag ambuja plus", "ambuja kawach 300", "2 ton kamdhenu 12mm", "50 bori ambuja plus aur 100 bag shree roc"
    def best_product(words: list[str]):
        said = set(words)
        best, best_s = None, 0.0
        for p in products:
            ptoks = set(re.findall(r"[a-z0-9]+", p["name"].lower()))
            hit = len(ptoks & said)
            if not hit:
                continue
            # most of the product's words said, fewest left over; similarity only breaks ties
            sc = hit / len(ptoks) + 0.5 * hit + 0.1 * SequenceMatcher(None, " ".join(words), p["name"].lower()).ratio()
            if sc > best_s:
                best, best_s = p, sc
        return best if best_s >= 0.8 else None

    # pieces split by commas, new lines, "aur" / "and" / "+": each piece is read on its own
    for piece in re.split(r"[,;\n+]|\baur\b|\band\b|\btatha\b", low):
        toks = re.findall(r"\d+(?:\.\d+)?(?![a-z\d])|[a-z0-9]+", piece)
        is_num = [bool(re.fullmatch(r"\d+(?:\.\d+)?", t)) for t in toks]

        used = set()
        for i, t in enumerate(toks):
            if not is_num[i]:
                continue
            j = i + 1
            unit_w = toks[j] if j < len(toks) and toks[j] in UNIT_WORDS else None
            if unit_w:
                j += 1
            after = []
            while j < len(toks) and not is_num[j] and len(after) < 5:
                after.append(toks[j])
                j += 1
            before_idx = [k for k in range(max(0, i - 4), i) if not is_num[k] and k not in used]
            before = [toks[k] for k in before_idx]
            # "200 bag ambuja": material after the unit; "ambuja plus 200": material before the number
            pa, pb = best_product(after), best_product(before)
            p, side = ((pa, "after") if pa else (pb, "before")) if unit_w else ((pb, "before") if pb else (pa, "after"))
            if p is None:
                continue
            used.update(range(i, j) if side == "after" else [*before_idx, i, *([i + 1] if unit_w else [])])
            unit = UNIT_WORDS.get(unit_w or "", p["default_unit"])
            lines.append({"product_id": p["id"], "said": " ".join(toks[max(0, i - 2):j]), "qty": float(t),
                          "unit": unit if unit in p["units"] else p["default_unit"], "rate": None})
    dealer_id = str(sender.id) if sender else (str(candidates[0].id) if candidates else None)
    delivery = today + timedelta(days=1) if re.search(r"\bkal\b|tomorrow", low) else \
        today + timedelta(days=2) if "parso" in low else None
    return {"is_order": bool(lines) or bool(re.search(r"\d", low)), "dealer_id": dealer_id, "dealer_hint": None, "lines": lines,
            "delivery_date": delivery.isoformat() if delivery else None,
            "urgent": bool(re.search(r"urgent|jaldi|abhi|turant", low)), "site": None, "notes": None,
            "unclear": [] if lines else ["Could not find material and quantity — read it and fill the order"]}


# ------------------------------------------------------------------ public
def read(db: Session, company_id: uuid.UUID, text: str, from_phone: str | None = None,
         today: date | None = None, only_dealers=None) -> tuple[dict, str]:
    """Returns (reading, reader) where reader is AI or RULES. The reading only holds ids from this company."""
    today = today or datetime.now(IST).date()
    products = products_for(db, company_id)
    sender = dealer_by_phone(db, company_id, from_phone)
    if sender is not None and only_dealers is not None and \
            db.execute(select(Dealer.id).where(Dealer.id == sender.id, Dealer.id.in_(only_dealers))).first() is None:
        sender = None  # not one of this person's dealers
    candidates = dealer_candidates(db, company_id, text, only=only_dealers)
    use_ai = bool(get_settings().anthropic_api_key)
    raw = _ai(text, sender, candidates, products, today) if use_ai else _rules(text, sender, candidates, products, today)
    return clean(raw, sender, candidates, products), ("AI" if use_ai else "RULES")


def clean(raw: dict, sender: Dealer | None, candidates: list[Dealer], products: list[dict]) -> dict:
    """Keep only ids that were offered, units the product has, sane numbers and dates."""
    ok_dealers = {str(d.id): d.name for d in candidates}
    if sender:
        ok_dealers[str(sender.id)] = sender.name
    byp = {p["id"]: p for p in products}
    did = raw.get("dealer_id") if raw.get("dealer_id") in ok_dealers else None
    if did is None and sender is not None:
        did = str(sender.id)
    lines, unclear = [], [str(u) for u in (raw.get("unclear") or []) if u][:8]
    for ln in raw.get("lines") or []:
        p = byp.get(ln.get("product_id") or "")
        try:
            qty = Decimal(str(ln.get("qty") or 0)).quantize(Decimal("0.001"))
        except InvalidOperation:
            qty = Decimal(0)
        try:
            rate = Decimal(str(ln["rate"])) if ln.get("rate") not in (None, "", 0) else None
        except InvalidOperation:
            rate = None
        unit = str(ln.get("unit") or "").upper()
        if p and unit not in p["units"]:
            unit = p["default_unit"]
        lines.append({"product_id": p["id"] if p else None, "product_name": p["name"] if p else None,
                      "said": str(ln.get("said") or "")[:120], "qty": str(qty), "unit": unit or None,
                      "rate": str(rate) if rate and rate > 0 else None})
    d = raw.get("delivery_date")
    try:
        d = date.fromisoformat(d).isoformat() if d else None
    except (TypeError, ValueError):
        d = None
    return {"is_order": bool(raw.get("is_order", True)), "dealer_id": did, "dealer_name": ok_dealers.get(did),
            "dealer_hint": raw.get("dealer_hint"), "lines": lines, "delivery_date": d,
            "urgent": bool(raw.get("urgent")), "site": (raw.get("site") or None),
            "notes": (raw.get("notes") or None), "unclear": unclear,
            "dealer_choices": [{"id": str(c.id), "name": c.name} for c in candidates[:6]]}
