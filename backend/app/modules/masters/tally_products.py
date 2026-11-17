"""Products from Tally stock items.

Tally knows the item name, its stock group and base unit, but not what the ERP needs for conversions
(brand, cement bag weight, TMT diameter). The import fills those in only where the name makes it clear,
and marks the rest "needs setup". Ordering and stock refuse products that are not set up.
"""

import re
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import snapshot, write_audit
from app.modules.masters.models import Brand, CompanyProduct, Product, ProductUnit
from app.modules.masters.schemas import TallyStockItemIn

# Keyword → (brand code, brand name, category, manufacturer). First match wins; order matters.
BRAND_RULES: list[tuple[str, tuple[str, str, str, str]]] = [
    (r"kamdhenu", ("KAMDHENU", "Kamdhenu Steel", "TMT", "Kamdhenu Ltd")),
    (r"ambuja", ("AMBUJA", "Ambuja Cement", "CEMENT", "Ambuja Cements Ltd")),
    (r"rock\s*strong|rockstrong", ("ROCKSTRONG", "Rockstrong", "CEMENT", "Shree Cement Ltd")),
    (r"bangur|roofon|shree|jung\s*rodhak", ("SHREE", "Shree Cement", "CEMENT", "Shree Cement Ltd")),
    (r"\bjsw\b", ("JSW", "JSW Cement", "CEMENT", "JSW Cement Ltd")),
    (r"wonder", ("WONDER", "Wonder Cement", "CEMENT", "Wonder Cement Ltd")),
    (r"ultra\s*tech", ("ULTRATECH", "UltraTech Cement", "CEMENT", "UltraTech Cement Ltd")),
    (r"jk\s*lakshmi", ("JKLAKSHMI", "JK Lakshmi Cement", "CEMENT", "JK Lakshmi Cement Ltd")),
    (r"\bjk\b", ("JK", "JK Cement", "CEMENT", "JK Cement Ltd")),
    (r"lafarge|nuvoco", ("LAFARGE", "Lafarge Cement", "CEMENT", "Nuvoco Vistas Corp Ltd")),
    (r"\bacc\b", ("ACC", "ACC Cement", "CEMENT", "ACC Ltd")),
]
# Bagged products that are not cement (adhesives, chemicals, plaster…): never classified as cement.
NOT_CEMENT = re.compile(r"adhesive|blockol|chemical|\bpop\b|plaster|putty|distemper|water\s*proof|grout|"
                        r"latex|primer|paint|tile|block|brick|cool\s*wall", re.I)
CEMENT_UNITS = {"BAG", "MT", "KG", "QTL"}
# Brand codes used in Ishika's TMT item names, e.g. "TMT 12MM (KD)".
TMT_BRAND_CODES = {
    "KD": ("KAMDHENU", "Kamdhenu Steel", "TMT", "Kamdhenu Ltd"),
    "JD": ("JINDAL", "Jindal", "TMT", "Jindal"),
    "BL": ("BIRLA", "Birla", "TMT", "Birla"),
    "R": ("RUNGTA", "Rungta", "TMT", "Rungta Steel"),
    "RT": ("RATHI", "Rathi", "TMT", "Rathi Steel"),
    "SS": ("SHYAM", "Shyam Steel", "TMT", "Shyam Steel Industries"),
}
TMT_CODE = re.compile(r"\(\s*(KD|JD|BL|RT|SS|R)\s*\)", re.I)
TMT_HINT = re.compile(r"\btmt\b|sariya|saria|\bfe\s?5\d\d|\b\d{1,2}\s?mm\b", re.I)
CEMENT_HINT = re.compile(r"cement|\bppc\b|\bopc\b|\bpsc\b|\bpcc\b", re.I)
UNIT_MAP = {"bag": "BAG", "bags": "BAG", "bori": "BAG", "mt": "MT", "ton": "MT", "tons": "MT", "tonne": "MT",
            "kg": "KG", "kgs": "KG", "qtl": "QTL", "quintal": "QTL", "pcs": "PC", "pc": "PC", "nos": "NOS",
            "no": "NOS", "bdl": "BDL", "bundle": "BDL", "cum": "CUM", "cbm": "CUM", "ft": "FT", "cft": "FT",
            "litre": "LTR", "liter": "LTR", "ltr": "LTR", "lt": "LTR", "pkt": "PKT", "packet": "PKT",
            "rickshaw": "RKS", "trolley": "TRIP", "trip": "TRIP"}


def map_unit(tally_unit: str | None) -> str | None:
    if not tally_unit:
        return None
    return UNIT_MAP.get(tally_unit.strip().strip(".").lower())


def classify(item: TallyStockItemIn) -> dict:
    """Best-effort reading of a Tally stock item. Returns product fields; unclear ones are left out."""
    text = " ".join(filter(None, [item.name, item.parent, item.category]))
    out: dict = {"brand": None, "category": "OTHER"}
    for pattern, brand in BRAND_RULES:
        if re.search(pattern, text, re.I):
            out["brand"] = brand
            out["category"] = brand[2]
            break
    unit = map_unit(item.base_unit)
    if out["category"] == "OTHER":
        if TMT_HINT.search(text):
            out["category"] = "TMT"
        elif CEMENT_HINT.search(text):
            out["category"] = "CEMENT"
    # Cement must be a bagged/weighed item and not one of the cement-brand side products.
    if out["category"] == "CEMENT" and (NOT_CEMENT.search(item.name) or unit not in CEMENT_UNITS):
        out["category"] = "OTHER"
    if out["category"] == "TMT" and out["brand"] is None:
        code = TMT_CODE.search(item.name)
        if code:
            out["brand"] = TMT_BRAND_CODES[code.group(1).upper()]
    if out["category"] == "TMT":
        dia = re.search(r"(?<!\d)(\d{1,2})\s?mm\b", text, re.I)
        if dia and 6 <= int(dia.group(1)) <= 40:
            out["diameter_mm"] = int(dia.group(1))
            out["length_m"] = Decimal("12")
            grade = re.search(r"\bfe\s?(5\d\d\s?d?)\b", text, re.I)
            out["tmt_grade"] = f"Fe{grade.group(1).replace(' ', '').upper()}" if grade else None
        else:
            out["category"] = "OTHER"  # TMT without a size cannot convert pieces/bundles; needs setup
    if out["category"] == "CEMENT":
        kg = re.search(r"\b(20|25|40|50)\s?kg\b", text, re.I)
        out["bag_weight_kg"] = Decimal(kg.group(1)) if kg else Decimal("50")
        grade = re.search(r"\b(opc\s?53|opc\s?43|opc|ppc|psc|pcc)\b", text, re.I)
        out["cement_grade"] = grade.group(1).upper().replace(" ", "") if grade else None
    # Everything else with a recognisable unit is an "other material" (blocks, bricks, dust, chemicals…).
    # TMT that lost its size above stays OTHER (needs setup), as does anything with an unknown unit.
    if out["category"] == "OTHER" and unit and not TMT_HINT.search(text):
        out["category"] = "GENERAL"
    out["base_uom"] = {"CEMENT": "BAG", "TMT": "KG"}.get(out["category"], unit or "PC")
    return out


def other_materials_enabled(db: Session, company_id: uuid.UUID) -> bool:
    from app.modules.platform.settings import get_setting

    return bool(get_setting(db, company_id, "products.other_materials_enabled"))


def enable_blocked_reason(db: Session, product: Product, cp: CompanyProduct, general_on: bool | None = None) -> str | None:
    if cp.tally_deleted:
        return "Deleted in Tally"
    if product.missing_setup():
        return f"Needs {', '.join(product.missing_setup())}"
    if product.category == "GENERAL":
        on = general_on if general_on is not None else other_materials_enabled(db, cp.company_id)
        if not on:
            return "Other materials go live in the next phase"
    return None


def _brand(db: Session, spec: tuple[str, str, str, str] | None) -> Brand | None:
    if spec is None:
        return None
    code, name, category, manufacturer = spec
    b = db.execute(select(Brand).where(Brand.code == code)).scalar_one_or_none()
    if b is None:
        b = Brand(code=code, name=name, category=category, manufacturer=manufacturer)
        db.add(b)
        db.flush()
    return b


def _product_code(name: str) -> str:
    base = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:30] or "ITEM"
    return f"T-{base}-{uuid.uuid4().hex[:5].upper()}"


def _default_units(db: Session, p: Product) -> None:
    units = {"CEMENT": ["BAG", "MT"], "TMT": ["KG", "MT", "PC", "BDL"]}.get(p.category, [p.base_uom])
    existing = {u.uom for u in p.units}
    for u in units:
        if u not in existing:
            db.add(ProductUnit(product_id=p.id, uom=u, is_default_entry=(u == units[0])))


def upsert_products_from_tally(db: Session, company_id: uuid.UUID, items: list[TallyStockItemIn],
                               actor_id: uuid.UUID | None, full: bool = False) -> dict:
    """Link or create one product per Tally stock item (keyed by GUID, else by Tally name).
    New items arrive switched ON if fully understood, OFF with 'needs setup' otherwise. A re-sync never
    changes the ERP switch or anything you set up; it only follows renames and flags deletions."""
    stats = {"created": 0, "linked": 0, "updated": 0, "unchanged": 0, "needs_setup": 0, "missing": 0}
    links = db.execute(select(CompanyProduct).where(CompanyProduct.company_id == company_id)).scalars().all()
    by_guid = {cp.tally_guid: cp for cp in links if cp.tally_guid}
    by_name = {(cp.tally_stock_item_name or "").strip().lower(): cp for cp in links if cp.tally_stock_item_name}
    seen = set()
    general_on = other_materials_enabled(db, company_id)
    for item in items:
        cp = (by_guid.get(item.guid) if item.guid else None) or by_name.get(item.name.strip().lower())
        if cp is not None:
            before = snapshot(cp)
            vals = {"tally_stock_item_name": item.name, "tally_parent": item.parent,
                    "tally_base_unit": item.base_unit, "tally_deleted": False}
            if item.guid:
                vals["tally_guid"] = item.guid
            was_unlinked = cp.tally_guid is None and item.guid is not None
            changed = {k: v for k, v in vals.items() if getattr(cp, k) != v}
            for k, v in changed.items():
                setattr(cp, k, v)
            if changed:
                db.flush()
                write_audit(db, action="product.linked_to_tally" if was_unlinked else "product.updated_from_tally",
                            entity_type="company_products", entity_id=cp.product_id, actor_id=actor_id,
                            company_id=company_id, before=before, after=snapshot(cp))
                stats["linked" if was_unlinked else "updated"] += 1
            else:
                stats["unchanged"] += 1
            seen.add(cp.product_id)
            continue

        info = classify(item)
        brand = _brand(db, info.pop("brand"))
        p = Product(code=_product_code(item.name), name=item.name, brand_id=brand.id if brand else None,
                    is_batch_tracked=info["category"] in ("CEMENT", "TMT"), created_by=actor_id, **info)
        db.add(p)
        db.flush()
        _default_units(db, p)
        ready = not p.missing_setup()
        live = ready and (p.category != "GENERAL" or general_on)
        cp = CompanyProduct(company_id=company_id, product_id=p.id, tally_stock_item_name=item.name,
                            tally_guid=item.guid, tally_parent=item.parent, tally_base_unit=item.base_unit,
                            source="TALLY", erp_enabled=live)
        db.add(cp)
        db.flush()
        write_audit(db, action="product.created_from_tally", entity_type="products", entity_id=p.id,
                    actor_id=actor_id, company_id=company_id, after=snapshot(p))
        stats["created"] += 1
        stats["needs_setup"] += 0 if ready else 1
        seen.add(p.id)
    if full:
        for cp in links:
            if cp.product_id not in seen and not cp.tally_deleted and cp.source in ("TALLY", "SEED"):
                before = snapshot(cp)
                cp.tally_deleted, cp.erp_enabled = True, False
                db.flush()
                write_audit(db, action="product.missing_in_tally", entity_type="company_products",
                            entity_id=cp.product_id, actor_id=actor_id, company_id=company_id,
                            before=before, after=snapshot(cp))
                stats["missing"] += 1
    return stats
