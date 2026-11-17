"""Purchase upload (ported from the Purchase Uploader app's app.py) for Ishika Traders Daily.

Flow, exactly as in the app: drop the Ambuja / Shree Excel files → each invoice is shown with stock item, godown,
bags, rate and amount → green "Ready" rows are pushed to Tally as Purchase vouchers; "Already in Tally" rows are
skipped (checked against Tally by invoice number and against the ERP's own push log); red rows need a mapping
rule, which is saved and applies to every later file.

New in the ERP: a pushed invoice that goes to the Direct godown becomes a Direct truck (delivered to dealers
straight from the truck), and Tally's godown stock is re-read after every push.
"""

import copy
import datetime as dt
import difflib
import json
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import AppError
from app.modules.masters.models import Company, CompanyProduct, Godown, Product
from app.modules.masters.tally_products import map_unit
from app.modules.platform.settings import get_setting
from app.modules.purchases import parsers, tally_io
from app.modules.purchases.models import (
    DirectTruck,
    DirectTruckItem,
    PurchaseFile,
    PurchasePost,
    PurchaseProfile,
)

# The profile used until the app's own settings are imported: the "main" (Ishika Traders Daily.) profile of the
# Purchase Uploader app as it stood on 15 Nov 2026.
DEFAULT_PROFILE: dict = {
    "name": "Ishika Traders Daily.",
    "company": "Ishika Traders Daily.",
    "tally_host": "",
    "tally_port": 9000,
    "bags_per_mt": 20,
    "gst": False,
    "voucher_type": "Purchase",
    "purchase_ledger": "PURCHASE",
    "use_bill_allocation": True,
    "gst_ledgers": {"cgst": "", "sgst": "", "igst": "", "round_off": ""},
    "shree_gst": {"rate": 18, "igst_sources": "PLANT"},
    "dealer_codes": {"ambuja": "", "shree": ""},
    "suppliers": {
        "ambuja": {
            "label": "Ambuja", "ledger": "Ambuja Cements Ltd. (TT)", "amount_field": "gross",
            "products": [{"match": "KAWACH", "item": "Ambuja Kawach"}, {"match": "PPC+|PLUS", "item": "Ambuja Plus"},
                         {"match": "PPC", "item": "Ambuja"}],
            "godowns": [{"match": "NLSK|NJAM|NE", "godown": "Direct"}, {"match": "NHJH", "godown": "Uttam Nagar"},
                        {"match": "NG60", "godown": "Nasirpur"}],
        },
        "shree": {
            "label": "Shree", "ledger": "Shree Cement Limited (A T Trading)",
            "products": [{"match": "ROOFON", "item": "ROOFON PPC"}, {"match": "OPC", "item": "Shree OPC"},
                         {"match": "JUNGRODHAK|PPC", "item": "Shree ROC"}],
            "godowns": [{"match": "TUGHLAKABAD", "godown": "SIDING"}, {"match": "KHUSHKHERA|KKG", "godown": "Direct"},
                        {"match": "NASIRPUR", "godown": "Nasirpur"}, {"match": "KAKROLA", "godown": "Uttam Nagar"},
                        {"match": "SHAKURBASTI", "godown": "Siding"}, {"match": "RAJNAGAR", "godown": "Direct"}],
        },
    },
}
PROFILE_KEYS = set(DEFAULT_PROFILE)
UPLOADER_DIR = Path(os.environ.get("ERP_PURCHASE_UPLOADER_DIR",
                                   str(Path.home() / "CascadeProjects" / "Purchase Uploader")))

_MASTERS: dict = {}  # company_id -> (loaded_at, masters)
_EXISTING: dict = {}  # company_id -> {"from", "to", "rows", "at"}
_LOCK = threading.Lock()
MASTERS_TTL = 15 * 60
EXISTING_TTL = 10 * 60


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- profile
def _norm_company(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def import_from_uploader(db: Session, company: Company, actor_id=None, folder: Path | None = None) -> dict:
    """Copy the Purchase Uploader app's settings (this company's profile and mapping rules) and its log of pushed
    invoices into the ERP, so nothing is pushed twice and no rule has to be set up again."""
    folder = Path(folder or UPLOADER_DIR) / "data"
    cfg_path, log_path = folder / "config.json", folder / "posted.json"
    if not cfg_path.exists():
        raise AppError(404, "UPLOADER_NOT_FOUND", f"Purchase Uploader settings not found at {cfg_path}")
    full = json.loads(cfg_path.read_text())
    want = _norm_company(company.tally_company_name or company.legal_name)
    pid, prof = None, None
    for k, p in (full.get("profiles") or {}).items():
        if _norm_company(p.get("company", "")) == want or _norm_company(p.get("name", "")) == want:
            pid, prof = k, p
            break
    if prof is None:
        raise AppError(404, "UPLOADER_PROFILE_NOT_FOUND",
                       f"The Purchase Uploader has no profile for {company.tally_company_name or company.legal_name}")
    cfg = copy.deepcopy(DEFAULT_PROFILE)
    cfg.update({k: v for k, v in prof.items() if k in PROFILE_KEYS})
    for k in ("tally_host", "tally_port", "bags_per_mt"):
        if full.get(k) not in (None, ""):
            cfg[k] = full[k]
    row = _profile_row(db, company.id, create=False)
    if row is None:
        db.add(PurchaseProfile(company_id=company.id, config=cfg, overrides={}, created_by=actor_id))
    else:
        row.config, row.updated_by = cfg, actor_id

    imported = 0
    if log_path.exists():
        log = json.loads(log_path.read_text()).get(cfg["company"], {})
        have = set(db.execute(select(PurchasePost.invoice_key).where(
            PurchasePost.company_id == company.id, PurchasePost.tally_company == cfg["company"])).scalars())
        for key, e in log.items():
            if key in have:
                continue
            db.add(PurchasePost(
                company_id=company.id, tally_company=cfg["company"], invoice_key=key, supplier=key.split(":", 1)[0],
                invoice_no=e.get("invoice_no") or key.split(":", 1)[-1],
                invoice_date=dt.date.fromisoformat(e["date"]) if e.get("date") else None,
                amount=Decimal(str(e["amount"])) if e.get("amount") is not None else None, vchid=e.get("vchid"),
                source="APP", posted_at=datetime.fromisoformat(e["at"]).replace(tzinfo=UTC) if e.get("at") else now()))
            imported += 1
    db.flush()
    write_audit(db, action="purchases.import_uploader", entity_type="purchase_profiles", entity_id=None,
                actor_id=actor_id, company_id=company.id, after={"profile": pid, "posted_imported": imported})
    return {"profile": pid, "posted_imported": imported}


def _profile_row(db: Session, company_id: uuid.UUID, create: bool = True) -> PurchaseProfile | None:
    row = db.execute(select(PurchaseProfile).where(PurchaseProfile.company_id == company_id)).scalar_one_or_none()
    if row is None and create:
        company = db.get(Company, company_id)
        try:  # first use: take over the app's settings when the app is on this computer
            import_from_uploader(db, company)
            row = db.execute(select(PurchaseProfile).where(PurchaseProfile.company_id == company_id)).scalar_one()
        except AppError:
            cfg = copy.deepcopy(DEFAULT_PROFILE)
            if company.tally_company_name:
                cfg["company"] = cfg["name"] = company.tally_company_name
            row = PurchaseProfile(company_id=company_id, config=cfg, overrides={})
            db.add(row)
            db.flush()
    return row


def load_cfg(db: Session, company_id: uuid.UUID) -> dict:
    row = _profile_row(db, company_id)
    cfg = copy.deepcopy(DEFAULT_PROFILE)
    cfg.update(copy.deepcopy(row.config))
    return cfg


def tally_url(db: Session, company_id: uuid.UUID, cfg: dict) -> str:
    from app.modules.integrations.tally.sync import normalize_url

    url = get_setting(db, company_id, "tally.url")
    if not url and cfg.get("tally_host"):
        url = f"{cfg['tally_host']}:{cfg.get('tally_port') or 9000}"
    if not url:
        raise tally_io.TallyError("Set the Tally address first (Overview → Sync with Tally, or Purchases → Settings).")
    return normalize_url(url)


def client(db: Session, company_id: uuid.UUID, cfg: dict) -> tally_io.Tally:
    return tally_io.Tally(tally_url(db, company_id, cfg), cfg.get("company", ""))


def masters(db: Session, company_id: uuid.UUID, cfg: dict, refresh: bool = False) -> dict | None:
    hit = _MASTERS.get(company_id)
    if hit and not refresh and time.time() - hit[0] < MASTERS_TTL:
        return hit[1]
    m = client(db, company_id, cfg).masters()
    _MASTERS[company_id] = (time.time(), m)
    return m


def forget_cache(company_id: uuid.UUID) -> None:
    _MASTERS.pop(company_id, None)
    _EXISTING.pop(company_id, None)


# ---------------------------------------------------------------- invoices
def _files(db: Session, company_id: uuid.UUID) -> list[PurchaseFile]:
    return list(db.execute(select(PurchaseFile).where(PurchaseFile.company_id == company_id,
                                                      PurchaseFile.cleared_at.is_(None))
                           .order_by(PurchaseFile.uploaded_at)).scalars())


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class Resolver:
    """Find Tally's exact spelling of a name, ignoring case, spaces and punctuation."""

    def __init__(self, names):
        self.exact = {n.lower(): n for n in names}
        self.loose = {}
        for n in names:
            self.loose.setdefault(_norm(n), []).append(n)
        self.names = names

    def get(self, name):
        if name.lower() in self.exact:
            return self.exact[name.lower()]
        hits = self.loose.get(_norm(name), [])
        return hits[0] if len(hits) == 1 else None

    def suggest(self, name, pool=None):
        pool = pool or self.names
        keyed = {_norm(n): n for n in pool}
        return [keyed[k] for k in difflib.get_close_matches(_norm(name), list(keyed), n=3, cutoff=0.5)]


def _existing(db: Session, company_id: uuid.UUID, cfg: dict, invs: list, check: bool) -> tuple[list | None, str]:
    """Purchase vouchers already in Tally over the invoices' dates (cached for a few minutes)."""
    if not invs:
        return None, ""
    dates = [dt.date.fromisoformat(i["date"]) for i in invs]
    lo, hi = min(dates), max(dates)
    hit = _EXISTING.get(company_id)
    if hit and hit["from"] <= lo and hit["to"] >= hi and time.time() - hit["at"] < EXISTING_TTL:
        return hit["rows"], ""
    if not check:
        return None, ""
    try:
        c = client(db, company_id, cfg)
        rows = c.existing_purchases(lo, hi)
        _EXISTING[company_id] = {"from": lo, "to": hi, "rows": rows, "at": time.time()}
        return rows, ""
    except tally_io.TallyError as e:
        return None, str(e)


def build_invoices(db: Session, company_id: uuid.UUID, check_tally: bool = False) -> dict:
    cfg = load_cfg(db, company_id)
    row = _profile_row(db, company_id)
    invs, errors, files = [], [], []
    for f in _files(db, company_id):
        try:
            supplier, rows = parsers.detect_and_parse(f.content, cfg)
            if f.supplier != supplier:
                f.supplier = supplier
            for r in rows:
                r["file"] = f.name
            invs.extend(rows)
        except Exception as e:  # noqa: BLE001 - one bad file must not hide the others
            errors.append(f"{f.name}: {e}")
        files.append({"id": f.id, "name": f.name, "supplier": f.supplier or ""})

    seen, uniq = set(), []
    for i in invs:  # the same invoice in two uploaded files
        if i["id"] not in seen:
            seen.add(i["id"])
            uniq.append(i)
    invs = uniq
    for i in invs:
        g = (row.overrides or {}).get(i["id"])
        if g:
            i["godown"], i["godown_overridden"] = g, True

    m, masters_err = None, ""
    if check_tally or _MASTERS.get(company_id):
        try:
            m = masters(db, company_id, cfg)
        except tally_io.TallyError as e:
            masters_err = str(e)
    existing, existing_err = _existing(db, company_id, cfg, invs, check_tally)
    validate(db, company_id, invs, cfg, m, existing)
    return {"invoices": invs, "file_errors": errors, "files": files, "masters": m, "cfg": cfg,
            "tally_checked": existing is not None, "tally_error": existing_err or masters_err}


def _direct_names(db: Session, company_id: uuid.UUID) -> set[str]:
    rows = db.execute(select(Godown.tally_godown_name, Godown.name).where(
        Godown.company_id == company_id, Godown.kind == "DIRECT")).all()
    return {(t or n).lower() for t, n in rows} | {"direct"}


def validate(db: Session, company_id: uuid.UUID, invs: list, cfg: dict, m: dict | None, existing: list | None) -> None:
    posted = {k: at for k, at in db.execute(select(PurchasePost.invoice_key, PurchasePost.posted_at).where(
        PurchasePost.company_id == company_id, PurchasePost.tally_company == cfg.get("company", "")))}
    trucks = set(db.execute(select(DirectTruck.invoice_key).where(DirectTruck.company_id == company_id)).scalars())
    direct = _direct_names(db, company_id)
    refs = set()
    for v in existing or []:
        for k in ("ref", "number"):
            if v[k]:
                refs.add(v[k].upper())
    R_items = Resolver([x["name"] for x in m["items"]]) if m else None
    R_gd = Resolver(m["godowns"]) if m else None
    R_led = Resolver([x["name"] for x in m["ledgers"]]) if m else None
    creditors = [x["name"] for x in m["ledgers"]] if m else []
    purch = cfg.get("purchase_ledger", "")
    purch_ok = R_led.get(purch) if (R_led and purch) else purch

    for i in invs:
        probs = []
        for ln in i["lines"]:
            if not ln["item"]:
                probs.append(f"No stock item mapped for “{ln['description']}”")
            elif R_items:
                hit = R_items.get(ln["item"])
                if hit:
                    ln["item"] = hit
                else:
                    sug = R_items.suggest(ln["item"])
                    probs.append(f"Stock item “{ln['item']}” not found in Tally"
                                 + (f" (closest: {', '.join(sug)})" if sug else ""))
            if not ln["bags"]:
                probs.append("Quantity is zero")
        if not i["godown"]:
            probs.append(f"No godown mapped for source “{parsers.source_label(i['source'])}”")
        elif R_gd:
            hit = R_gd.get(i["godown"])
            if hit:
                i["godown"] = hit
            else:
                probs.append(f"Godown “{i['godown']}” not found in Tally")
        if R_led:
            hit = R_led.get(i["party_ledger"])
            if hit:
                i["party_ledger"] = hit
            else:
                i["ledger_missing"] = True
                sug = R_led.suggest(i["party_ledger"], creditors)
                probs.append(f"Supplier ledger “{i['party_ledger']}” not found in Tally"
                             + (f" (closest: {', '.join(sug)})" if sug else ""))
        if not purch:
            probs.append("Purchase ledger not set in Settings")
        elif not purch_ok:
            probs.append(f"Purchase ledger “{purch}” not found in Tally")
        i["purchase_ledger"] = purch_ok or purch

        if i.get("gst_mode"):
            gl = cfg.get("gst_ledgers", {})
            tl = {}
            need = [k for k in ("cgst", "sgst", "igst") if abs(i.get(k, 0)) >= 0.005]
            if abs(i.get("round_off", 0)) >= 0.005:
                need.append("round_off")
            for k in need:
                name = gl.get(k, "")
                label = {"cgst": "CGST", "sgst": "SGST", "igst": "IGST", "round_off": "Round off"}[k]
                if not name:
                    probs.append(f"{label} ledger not set in Settings")
                elif R_led and not R_led.get(name):
                    probs.append(f"{label} ledger “{name}” not found in Tally")
                tl[k] = (R_led.get(name) if R_led else None) or name
            i["tax_ledgers"] = tl
        elif abs(i.get("round_off", 0)) >= 0.005:
            i["tax_ledgers"] = {"round_off": cfg.get("gst_ledgers", {}).get("round_off", "")}

        codes = [c.strip() for c in (cfg.get("dealer_codes", {}).get(i["supplier"], "") or "").split("|") if c.strip()]
        if i.get("dealer_code") and codes and i["dealer_code"] not in codes:
            probs.insert(0, f"Dealer code {i['dealer_code']} is not set for this company")

        i["direct"] = (i.get("godown") or "").lower() in direct
        i["direct_truck"] = i["id"] in trucks
        if i["id"] in posted:
            i["status"] = "posted"
            i["status_note"] = f"Pushed on {posted[i['id']].astimezone().strftime('%d %b %Y %H:%M')}"
        elif i["invoice_no"].upper() in refs:
            i["status"] = "exists"
            i["status_note"] = "Already in Tally"
        elif probs:
            i["status"] = "problem"
            i["status_note"] = probs[0]
        else:
            i["status"] = "ready"
            i["status_note"] = "Ready" if existing is not None else "Ready (Tally duplicate check not run)"
        i["problems"] = probs


def unmapped(invs: list, m: dict | None) -> list:
    out = {"products": {}, "godowns": {}}
    ledgers = {}
    for i in invs:
        for ln in i["lines"]:
            if not ln["item"]:
                k = f"{i['supplier']}|{ln['description']}"
                out["products"][k] = out["products"].get(k, 0) + 1
        if not i["godown"]:
            k = f"{i['supplier']}|{i['source']}"
            out["godowns"][k] = out["godowns"].get(k, 0) + 1
        if i.get("ledger_missing") and i["status"] == "problem":
            ledgers.setdefault(i["supplier"], [i["party_ledger"], 0])[1] += 1
    res = []
    for sup, (name, n) in ledgers.items():
        names = [x["name"] for x in m["ledgers"]] if m else []
        creditors = [x["name"] for x in m["ledgers"] if "creditor" in x["parent"].lower()] if m else []
        res.append({"kind": "ledger", "supplier": sup, "text": name, "count": n,
                    "suggest": Resolver(names).suggest(name, creditors or names) if names else [],
                    "options": sorted(creditors or names, key=str.lower)})
    return res + [{"kind": "product", "supplier": k.split("|", 1)[0], "text": k.split("|", 1)[1], "count": n}
                  for k, n in out["products"].items()] + \
                 [{"kind": "godown", "supplier": k.split("|", 1)[0], "text": k.split("|", 1)[1],
                   "label": parsers.source_label(k.split("|", 1)[1]), "count": n}
                  for k, n in out["godowns"].items()]


def payload(db: Session, company_id: uuid.UUID, check_tally: bool = False) -> dict:
    b = build_invoices(db, company_id, check_tally)
    m, cfg = b["masters"], b["cfg"]
    return {
        "invoices": b["invoices"],
        "file_errors": b["file_errors"],
        "files": b["files"],
        "unmapped": unmapped(b["invoices"], m),
        "tally_checked": b["tally_checked"],
        "tally_error": b["tally_error"],
        "company": cfg.get("company", ""),
        "gst": bool(cfg.get("gst")),
        "options": {"items": [x["name"] for x in m["items"]] if m else [],
                    "godowns": m["godowns"] if m else []},
    }


def rule_keyword(supplier: str, text: str) -> str:
    """Pick a stable keyword for a new mapping rule from the raw text."""
    label = parsers.source_label(text).upper()
    label = re.sub(r"^(DL|SCL)[\s-]+", "", label)
    label = re.sub(r"\s+(TR|C RD|PLANT|DEPOT)$", "", label).strip()
    return label or text.upper()


def save_rule(db: Session, company_id: uuid.UUID, b, actor_id) -> None:
    row = _profile_row(db, company_id)
    cfg = copy.deepcopy(row.config)
    sc = cfg["suppliers"][b.supplier]
    if b.kind == "ledger":
        sc["ledger"] = b.value
    elif b.kind == "product":
        sc["products"].insert(0, {"match": (b.keyword or b.text).upper(), "item": b.value})
    else:
        if b.supplier == "ambuja":
            kw = (b.keyword or b.text[:4]).upper()
        else:
            kw = (b.keyword or rule_keyword("shree", b.text)).upper()
        sc["godowns"].insert(0, {"match": kw, "godown": b.value})
    row.config, row.updated_by = cfg, actor_id
    write_audit(db, action="purchases.rule", entity_type="purchase_profiles", entity_id=row.id, actor_id=actor_id,
                company_id=company_id, after=b.model_dump())


def set_override(db: Session, company_id: uuid.UUID, invoice_id: str, godown: str | None) -> None:
    row = _profile_row(db, company_id)
    ov = dict(row.overrides or {})
    if godown:
        ov[invoice_id] = godown
    else:
        ov.pop(invoice_id, None)
    row.overrides = ov


def clear(db: Session, company_id: uuid.UUID, file_id: uuid.UUID | None = None) -> None:
    for f in _files(db, company_id):
        if file_id is None or f.id == file_id:
            f.cleared_at = now()
    if file_id is None:
        _profile_row(db, company_id).overrides = {}
    _EXISTING.pop(company_id, None)


def invoice_xml(db: Session, company_id: uuid.UUID, invoice_id: str) -> str:
    b = build_invoices(db, company_id, check_tally=False)
    inv = next((i for i in b["invoices"] if i["id"] == invoice_id), None)
    if inv is None:
        raise AppError(404, "NOT_FOUND", "Invoice is no longer loaded")
    units = {x["name"]: x["unit"] for x in (b["masters"] or {}).get("items", [])}
    return tally_io.purchase_voucher_xml(inv, b["cfg"], units)


def push(db: Session, company_id: uuid.UUID, ids: list[str], actor_id) -> list[dict]:
    cfg = load_cfg(db, company_id)
    c = client(db, company_id, cfg)
    m = masters(db, company_id, cfg)
    units = {x["name"]: x["unit"] for x in m["items"]}
    with _LOCK:  # two people pressing Push at once must not create the same voucher twice
        _EXISTING.pop(company_id, None)
        b = build_invoices(db, company_id, check_tally=True)
        if not b["tally_checked"]:
            raise AppError(502, "TALLY_UNREACHABLE", b["tally_error"] or "Could not check Tally for duplicates")
        by_id = {i["id"]: i for i in b["invoices"]}
        results = []
        for _id in ids:
            i = by_id.get(_id)
            if not i:
                results.append({"id": _id, "ok": False, "error": "Invoice no longer loaded"})
                continue
            if i["status"] in ("posted", "exists"):
                results.append({"id": _id, "ok": False, "skipped": True, "error": i["status_note"]})
                continue
            if i["status"] == "problem":
                results.append({"id": _id, "ok": False, "error": i["status_note"]})
                continue
            xml = tally_io.purchase_voucher_xml(i, cfg, units)
            try:
                r = c.import_voucher(xml)
            except tally_io.TallyError as e:
                r = {"ok": False, "error": str(e)}
            if r["ok"]:
                db.add(PurchasePost(
                    company_id=company_id, tally_company=cfg.get("company", ""), invoice_key=i["id"],
                    supplier=i["supplier"], invoice_no=i["invoice_no"], invoice_date=dt.date.fromisoformat(i["date"]),
                    amount=Decimal(str(i["amount"])), godown=i["godown"], truck_no=i.get("truck") or None,
                    vchid=r.get("vchid") or None, source="ERP", posted_by=actor_id, posted_at=now()))
                truck = None
                if i["direct"]:
                    truck = add_direct_truck(db, company_id, i, units, actor_id)
                write_audit(db, action="purchases.push", entity_type="purchase_posts", entity_id=None,
                            actor_id=actor_id, company_id=company_id,
                            after={"invoice": i["id"], "amount": i["amount"], "godown": i["godown"],
                                   "vchid": r.get("vchid"), "direct_truck": truck.truck_no if truck else None})
                db.commit()  # each voucher is final once Tally has it
            results.append({"id": _id, **r})
        _EXISTING.pop(company_id, None)
    return results


def forget(db: Session, company_id: uuid.UUID, ids: list[str] | None, actor_id) -> int:
    """Forget that the ERP pushed these invoices (e.g. after deleting the vouchers in Tally) so they can be pushed
    again. Tally is still checked for duplicates."""
    cfg = load_cfg(db, company_id)
    if ids is None:
        ids = [i["id"] for i in build_invoices(db, company_id)["invoices"]]
    rows = db.execute(select(PurchasePost).where(
        PurchasePost.company_id == company_id, PurchasePost.tally_company == cfg.get("company", ""),
        PurchasePost.invoice_key.in_(ids))).scalars().all()
    for r in rows:
        db.delete(r)
    write_audit(db, action="purchases.forget", entity_type="purchase_posts", entity_id=None, actor_id=actor_id,
                company_id=company_id, after={"invoices": [r.invoice_key for r in rows]})
    _EXISTING.pop(company_id, None)
    return len(rows)


# ---------------------------------------------------------------- direct trucks
def product_by_tally_item(db: Session, company_id: uuid.UUID) -> dict[str, Product]:
    rows = db.execute(select(CompanyProduct.tally_stock_item_name, Product).join(
        Product, Product.id == CompanyProduct.product_id).where(
        CompanyProduct.company_id == company_id, CompanyProduct.tally_stock_item_name.is_not(None))).all()
    return {n.lower(): p for n, p in rows}


def to_base(db: Session, product: Product | None, qty: Decimal, uom: str | None) -> Decimal | None:
    from app.modules.masters.conversions import convert

    if product is None or not uom:
        return None
    if uom == product.base_uom:
        return qty
    try:
        return convert(db, product, qty, uom).result
    except AppError:
        return None


def _godown_for(db: Session, company_id: uuid.UUID, tally_name: str) -> Godown | None:
    gs = db.execute(select(Godown).where(Godown.company_id == company_id)).scalars().all()
    for g in gs:
        if (g.tally_godown_name or g.name).lower() == tally_name.lower():
            return g
    return next((g for g in gs if g.kind == "DIRECT"), None)


def add_direct_truck(db: Session, company_id: uuid.UUID, inv: dict, units: dict, actor_id) -> DirectTruck:
    existing = db.execute(select(DirectTruck).where(DirectTruck.company_id == company_id,
                                                    DirectTruck.invoice_key == inv["id"])).scalar_one_or_none()
    if existing:
        return existing
    godown = _godown_for(db, company_id, inv["godown"])
    if godown is None:
        raise AppError(422, "NO_DIRECT_GODOWN", "There is no Direct godown in the ERP yet. Run a Tally sync first.")
    products = product_by_tally_item(db, company_id)
    truck = DirectTruck(company_id=company_id, invoice_key=inv["id"], supplier=inv["supplier"],
                        invoice_no=inv["invoice_no"], invoice_date=dt.date.fromisoformat(inv["date"]),
                        truck_no=(inv.get("truck") or "").upper().replace(" ", "") or inv["invoice_no"],
                        godown_id=godown.id, source=inv.get("source") or None, ref=inv.get("ref") or None,
                        status="OPEN", created_by=actor_id)
    for ln in inv["lines"]:
        p = products.get(ln["item"].lower())
        uom = map_unit(units.get(ln["item"])) or "BAG"
        q = Decimal(str(ln["bags"]))
        truck.items.append(DirectTruckItem(tally_item=ln["item"], product_id=p.id if p else None, qty=q, uom=uom,
                                           base_qty=to_base(db, p, q, uom)))
    db.add(truck)
    db.flush()
    return truck


def add_truck_from_invoice(db: Session, company_id: uuid.UUID, invoice_id: str, actor_id) -> DirectTruck:
    """For a Direct invoice that is already in Tally (pushed before the ERP took over, or by hand)."""
    b = build_invoices(db, company_id, check_tally=False)
    inv = next((i for i in b["invoices"] if i["id"] == invoice_id), None)
    if inv is None:
        raise AppError(404, "NOT_FOUND", "Invoice is no longer loaded")
    if not inv["direct"]:
        raise AppError(422, "NOT_DIRECT", f"This invoice goes to {inv['godown'] or 'no godown'}, not Direct")
    units = {x["name"]: x["unit"] for x in (b["masters"] or {}).get("items", [])}
    truck = add_direct_truck(db, company_id, inv, units, actor_id)
    write_audit(db, action="direct_truck.added", entity_type="direct_trucks", entity_id=truck.id, actor_id=actor_id,
                company_id=company_id, after={"invoice": invoice_id, "truck": truck.truck_no})
    return truck
