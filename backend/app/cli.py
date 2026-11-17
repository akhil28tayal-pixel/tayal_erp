"""Management commands.

  python -m app.cli seed-base                      roles, permissions, units (safe to re-run)
  python -m app.cli seed-company                   Ishika Traders Daily + own-stock owner + own fleet
  python -m app.cli sync-tally-masters --tally-url http://<tally-pc>:9000 [--dry-run]
                                                   customers (Sundry Debtors) + godowns from Tally
  python -m app.cli seed-catalog                   brands/products/conversions known so far
  python -m app.cli create-admin --phone 98xxxxxxxx --name "Akhil"   (password from ERP_ADMIN_PASSWORD or prompt)
  python -m app.cli seed-demo                      separate "DEMO Trading Co" with fake dealers — never real data

Every command is idempotent: it inserts what is missing and leaves existing rows alone.
"""

import argparse
import getpass
import os
import sys
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.db import SessionLocal
from app.core.permissions import PERMISSIONS, ROLE_DEFAULTS
from app.core.security import hash_password
from app.modules.identity.models import Permission, Role, RolePermission, User, UserRole
from app.modules.masters.models import (
    Brand,
    Company,
    CompanyProduct,
    Employee,
    InventoryOwner,
    Product,
    ProductConversion,
    ProductUnit,
    Transporter,
    Uom,
    Vehicle,
)
from app.modules.masters.schemas import TallyGodownIn, TallyLedgerIn
from app.modules.masters.service import (
    assign_dealers,
    upsert_dealers_from_tally,
    upsert_godowns_from_tally,
)

COMPANY_CODE = "ITD"


def _get_or_create(db: Session, model, lookup: dict, **values):
    obj = db.execute(select(model).filter_by(**lookup)).scalar_one_or_none()
    if obj is None:
        obj = model(**lookup, **values)
        db.add(obj)
        db.flush()
    return obj


def seed_base(db: Session) -> None:
    for code, desc in PERMISSIONS.items():
        perm = db.get(Permission, code)
        if perm is None:
            db.add(Permission(code=code, module=code.split(".")[0], description=desc))
        else:
            perm.description = desc
    db.flush()
    for code, (name, perms) in ROLE_DEFAULTS.items():
        role = _get_or_create(db, Role, {"code": code}, name=name, is_system=True)
        role.name = name
        have = set(db.execute(select(RolePermission.permission_code).where(RolePermission.role_id == role.id))
                   .scalars())
        for p in perms - have:
            db.add(RolePermission(role_id=role.id, permission_code=p))
        # System roles follow the code exactly, so tightening a role (e.g. Sales = orders only) takes effect.
        for p in have - perms:
            db.execute(delete(RolePermission).where(RolePermission.role_id == role.id,
                                                    RolePermission.permission_code == p))
    for code, name, kind in [
        ("BAG", "Bag", "COUNT"), ("MT", "Metric tonne", "WEIGHT"), ("KG", "Kilogram", "WEIGHT"),
        ("QTL", "Quintal", "WEIGHT"), ("PC", "Piece", "COUNT"), ("BDL", "Bundle", "COUNT"),
        ("M", "Metre", "LENGTH"),
        # units used by other building materials in Tally
        ("CUM", "Cubic metre", "VOLUME"), ("FT", "Ft (as in Tally)", "VOLUME"), ("LTR", "Litre", "VOLUME"),
        ("PKT", "Packet", "COUNT"), ("NOS", "Number", "COUNT"), ("RKS", "Rickshaw load", "LOAD"),
        ("TRIP", "Trolley / truck load", "LOAD"),
    ]:
        if db.get(Uom, code) is None:
            db.add(Uom(code=code, name=name, kind=kind))
    db.flush()
    for frm, to, f in [("MT", "KG", Decimal("1000")), ("QTL", "KG", Decimal("100"))]:
        _get_or_create(db, ProductConversion,
                       {"from_uom": frm, "to_uom": to, "product_id": None, "brand_id": None, "category": None},
                       factor=f, source_note="global")
    print("base: permissions, roles, units ready")


def seed_company(db: Session) -> Company:
    """Decision D1: one company, Ishika Traders Daily (non-GST books)."""
    company = _get_or_create(
        db, Company, {"code": COMPANY_CODE}, legal_name="Ishika Traders Daily", trade_name="Ishika Traders Daily",
        doc_prefix=COMPANY_CODE, tally_company_name="Ishika Traders Daily", is_gst_registered=False,
        state_code="07",
    )
    _get_or_create(db, InventoryOwner, {"company_id": company.id, "name": "Ishika Traders Daily – own stock"},
                   kind="OWN_TRADING")
    # Decision: trucks are owned. Godowns are NOT seeded — they come from Tally (sync-tally-masters).
    _get_or_create(db, Transporter, {"company_id": company.id, "name": "Own fleet"}, is_own_fleet=True)
    from app.modules.identity.service import backfill_employees

    made = backfill_employees(db, company.id)
    if made:
        print(f"employees: created for {made} existing user(s)")
    print(f"company: {company.legal_name} ({company.id})")
    return company


# IS 1786 nominal mass per metre. Replace with the manufacturer's own chart when available.
TMT_KG_PER_M = {8: "0.395", 10: "0.617", 12: "0.888", 16: "1.580", 20: "2.470", 25: "3.850", 32: "6.310"}


BRANDS = {
    "AMBUJA": ("Ambuja Cement", "CEMENT", "Ambuja Cements Ltd"),
    "SHREE": ("Shree Cement", "CEMENT", "Shree Cement Ltd"),
    "JSW": ("JSW Cement", "CEMENT", "JSW Cement Ltd"),
    "KAMDHENU": ("Kamdhenu Steel", "TMT", "Kamdhenu Ltd"),
    "JINDAL": ("Jindal", "TMT", "Jindal"),
    "BIRLA": ("Birla", "TMT", "Birla"),
    "RUNGTA": ("Rungta", "TMT", "Rungta Steel"),
    "RATHI": ("Rathi", "TMT", "Rathi Steel"),
    "SHYAM": ("Shyam Steel", "TMT", "Shyam Steel Industries"),
}


def seed_catalog(db: Session) -> None:
    """Brands and the TMT weight chart. Products themselves come from Tally (sync-tally-masters)."""
    for code, (n, c, m) in BRANDS.items():
        _get_or_create(db, Brand, {"code": code}, name=n, category=c, manufacturer=m)
    for dia, kgm in TMT_KG_PER_M.items():
        _get_or_create(db, ProductConversion,
                       {"category": "TMT", "brand_id": None, "product_id": None, "diameter_mm": dia,
                        "from_uom": "M", "to_uom": "KG"},
                       factor=Decimal(kgm), basis="NOMINAL",
                       source_note="IS 1786 nominal for all TMT brands; add a brand chart to override")
    db.flush()
    print("catalog: brands + TMT weight chart ready (products come from Tally)")


def seed_sample_products(db: Session, company: Company) -> None:
    """Sample products for the DEMO company and for tests. Never used for the real company."""
    seed_catalog(db)
    b = {br.code: br for br in db.execute(select(Brand)).scalars()}
    cement = [
        ("AMB-PPC", "Ambuja PPC", "AMBUJA", "PPC", "50", "Ambuja"),
        ("AMB-KAWACH", "Ambuja Kawach", "AMBUJA", "PPC", "50", "Ambuja Kawach"),
        ("AMB-PLUS", "Ambuja Plus", "AMBUJA", "PPC", "50", "Ambuja Plus"),
        ("SHR-ROC", "Shree Jung Rodhak PPC", "SHREE", "PPC", "50", "Shree ROC"),
        ("SHR-OPC", "Shree Jung Rodhak OPC", "SHREE", "OPC53", "50", "Shree OPC"),
        ("SHR-ROOFON", "Bangur Roofon PPC", "SHREE", "PPC", "50", "ROOFON PPC"),
    ]
    for code, name, brand, grade, kg, tally in cement:
        p = _get_or_create(db, Product, {"code": code}, name=name, brand_id=b[brand].id, category="CEMENT",
                           base_uom="BAG", cement_grade=grade, bag_weight_kg=Decimal(kg), is_batch_tracked=True)
        for uom in ("BAG", "MT"):
            if db.get(ProductUnit, (p.id, uom)) is None:
                db.add(ProductUnit(product_id=p.id, uom=uom, is_default_entry=uom == "BAG"))
        if db.get(CompanyProduct, (company.id, p.id)) is None:
            db.add(CompanyProduct(company_id=company.id, product_id=p.id, tally_stock_item_name=tally,
                                  source="DEMO"))
    for dia in TMT_KG_PER_M:
        p = _get_or_create(db, Product, {"code": f"KAM-FE550D-{dia}"}, name=f"Kamdhenu Fe550D {dia}mm",
                           brand_id=b["KAMDHENU"].id, category="TMT", base_uom="KG", tmt_grade="Fe550D",
                           diameter_mm=dia, length_m=Decimal("12"), is_batch_tracked=True)
        for uom in ("KG", "MT", "PC", "BDL"):
            if db.get(ProductUnit, (p.id, uom)) is None:
                db.add(ProductUnit(product_id=p.id, uom=uom, is_default_entry=uom == "MT"))
        if db.get(CompanyProduct, (company.id, p.id)) is None:
            db.add(CompanyProduct(company_id=company.id, product_id=p.id, source="DEMO"))
    db.flush()


def create_admin(db: Session, phone: str, name: str) -> None:
    company = db.execute(select(Company).where(Company.code == COMPANY_CODE)).scalar_one()
    password = os.environ.get("ERP_ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    if len(password) < 8:
        sys.exit("password must be at least 8 characters")
    if not os.environ.get("ERP_ADMIN_PASSWORD") and getpass.getpass("Type it again: ") != password:
        sys.exit("the two passwords did not match; nothing changed")
    user = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
    if user is not None:
        print(f"{phone} already exists, so its password was NOT changed (use reset-password for that)")
    if user is None:
        user = User(phone=phone, full_name=name, password_hash=hash_password(password))
        db.add(user)
        db.flush()
    for code in ("SUPER_ADMIN", "BUSINESS_OWNER"):
        role = db.execute(select(Role).where(Role.code == code)).scalar_one()
        if db.get(UserRole, (user.id, role.id, company.id)) is None:
            db.add(UserRole(user_id=user.id, role_id=role.id, company_id=company.id))
    db.flush()
    from app.modules.identity.service import ensure_employee

    ensure_employee(db, user, company.id, None)
    print(f"admin: {phone} has SUPER_ADMIN + BUSINESS_OWNER in {company.legal_name}")


def reset_password(db: Session, phone: str | None) -> None:
    """Set a new password for a user (forgotten password). Also unlocks the account and signs out old sessions."""
    from app.modules.identity.service import revoke_all_sessions

    users = db.execute(select(User).order_by(User.full_name)).scalars().all()
    if not phone:
        print("Users:")
        for u in users:
            print(f"  {u.phone:<14} {u.full_name}{'' if u.is_active else '  (switched off)'}")
        phone = input("Phone of the user to reset: ").strip()
    user = next((u for u in users if u.phone == phone), None)
    if user is None:
        sys.exit(f"no user with phone {phone}")
    password = os.environ.get("ERP_NEW_PASSWORD") or getpass.getpass(f"New password for {user.full_name}: ")
    if len(password) < 8:
        sys.exit("password must be at least 8 characters")
    if not os.environ.get("ERP_NEW_PASSWORD") and getpass.getpass("Type it again: ") != password:
        sys.exit("the two passwords did not match; nothing changed")
    user.password_hash = hash_password(password)
    user.failed_logins, user.locked_until, user.is_active = 0, None, True
    revoke_all_sessions(db, user, "password_reset_cli")
    print(f"Password changed for {user.full_name} ({user.phone}). Log in with the phone number and the new password.")


def seed_demo(db: Session) -> None:
    """Fake data in its own company. It never touches Ishika Traders Daily."""
    demo = _get_or_create(db, Company, {"code": "DEMO"}, legal_name="DEMO Trading Co (sample data)",
                          doc_prefix="DEMO", is_demo=True)
    _get_or_create(db, InventoryOwner, {"company_id": demo.id, "name": "DEMO own stock"}, kind="OWN_TRADING")
    upsert_godowns_from_tally(db, demo.id, [TallyGodownIn(guid=f"demo-g-{n}", name=n)
                                            for n in ("Direct", "Demo Godown A", "Demo Godown B")],
                              actor_id=None, source="DEMO")
    exec_ = _get_or_create(db, Employee, {"company_id": demo.id, "code": "E-SALES"}, full_name="Demo Sales Exec")
    _get_or_create(db, Employee, {"company_id": demo.id, "code": "E-COLL"}, full_name="Demo Collection Exec")
    stats = upsert_dealers_from_tally(
        db, demo.id,
        [TallyLedgerIn(guid=f"demo-guid-{i}", name=f"Demo Dealer {i}", parent="Sundry Debtors",
                       credit_limit=Decimal(200000 * i), credit_period_days=15) for i in range(1, 6)],
        actor_id=None, source="DEMO",
    )
    from app.modules.masters.models import Dealer

    ids = list(db.execute(select(Dealer.id).where(Dealer.company_id == demo.id).limit(3)).scalars())
    assign_dealers(db, demo.id, ids, exec_.id, "SALES", "demo seed", actor_id=exec_.id)
    seed_sample_products(db, demo)
    t = _get_or_create(db, Transporter, {"company_id": demo.id, "name": "Demo Roadways"})
    _get_or_create(db, Vehicle, {"company_id": demo.id, "registration_no": "DL01AB1234"},
                   vehicle_type="TRUCK_10W", capacity_kg=Decimal("16000"), transporter_id=t.id)
    print(f"demo: {demo.legal_name} ready, dealers {stats}")


from app.modules.integrations.tally.sync import match_company_name  # noqa: E402,F401  (kept for imports)


def sync_tally_masters(db: Session, tally_url: str, dry_run: bool = False, save_xml: str | None = None) -> None:
    """Pull customers and godowns straight from TallyPrime (e.g. over Tailscale). Full sync: anything that
    disappeared from Tally is flagged, never deleted. The Windows connector (Step 6) will reuse the same
    upsert functions; this command is for onboarding and for running before the connector exists."""
    from app.modules.integrations.tally.xml_client import TallyClient, TallyError

    company = db.execute(select(Company).where(Company.code == COMPANY_CODE)).scalar_one()
    client = TallyClient(tally_url)
    if save_xml:
        # Keep Tally's raw replies so parsing problems can be diagnosed from the real data.
        from pathlib import Path

        out = Path(save_xml)
        out.mkdir(parents=True, exist_ok=True)
        original_post = client.post

        def saving_post(body: str) -> str:
            import re as _re

            reply = original_post(body)
            rid = (_re.search(r"<ID>(\w+)</ID>", body) or [None, "request"])[1]
            (out / f"{rid}.xml").write_text(reply, encoding="utf-8")
            return reply

        client.post = saving_post  # type: ignore[method-assign]
        print(f"Saving Tally's replies to {out.resolve()}")
    try:
        loaded = client.companies()
        tally_name = match_company_name(company.tally_company_name or company.legal_name, loaded)
        if tally_name is None:
            sys.exit(f"Company '{company.tally_company_name}' is not open in Tally. Open companies: {loaded}")
        if tally_name != company.tally_company_name:
            print(f"Using Tally's exact company name: '{tally_name}'")
            company.tally_company_name = tally_name  # requests must use Tally's exact spelling
        ledgers = client.ledgers(tally_name)
        godowns = client.godowns(tally_name)
        items = client.stock_items(tally_name)
    except TallyError as e:
        sys.exit(str(e))
    print(f"Tally: {len(ledgers)} customer ledgers, {len(godowns)} godowns, {len(items)} stock items")
    if not ledgers and not godowns and not items:
        print("Nothing recognised in Tally's reply. Re-run with --save-xml tally-replies and share that folder.")
    if dry_run:
        for g in godowns:
            print("  godown:", g.name)
        for lg in ledgers[:20]:
            print("  ledger:", lg.name, "|", lg.parent)
        from app.modules.masters.tally_products import classify

        for it in items:
            c = classify(it)
            size = c.get("bag_weight_kg") and f"{c['bag_weight_kg']}kg bag" or c.get("diameter_mm") and f"{c['diameter_mm']}mm" or ""
            brand = c["brand"][1] if c["brand"] else "brand?"
            print(f"  item: {it.name} | {it.parent} | {it.base_unit}  →  {c['category'].lower()}, {brand} {size}")
        print("DRY RUN: nothing was saved. Run again without --dry-run to save.")
        return
    from app.modules.integrations.tally import sync

    summary = sync.apply(db, company, tally_name, ledgers, godowns, items, actor_id=None)
    sync.save_url(db, company.id, sync.normalize_url(tally_url), None)  # the web "Sync with Tally" button reuses it
    print(f"dealers {summary['dealers']}\ngodowns {summary['godowns']}\nproducts {summary['products']}")
    print("Next, in the web app: switch off unused godowns and products, and finish products marked 'Needs setup'.")


def remove_sample_products(db: Session, dry_run: bool = False) -> dict:
    """Remove products created by the early seed (sample catalog) and any DEMO-only products, so the real
    company only has products fetched from Tally. Products that orders/rates already point to are kept and
    reported, never deleted silently. Run sync-tally-masters afterwards to (re)create items from Tally."""
    from sqlalchemy import delete, func

    from app.modules.masters.models import DealerRate

    company = db.execute(select(Company).where(Company.code == COMPANY_CODE)).scalar_one()
    sample_links = db.execute(
        select(CompanyProduct).where(CompanyProduct.company_id == company.id, CompanyProduct.source == "SEED")
    ).scalars().all()
    report = {"removed": [], "converted": [], "kept_in_use": []}
    for cp in sample_links:
        p = cp.product
        rates = db.execute(select(func.count()).select_from(DealerRate).where(DealerRate.product_id == p.id)).scalar_one()
        if rates and cp.tally_guid:
            # In use and already linked to a Tally item: becomes that Tally item (Tally's name), rates kept.
            report["converted"].append(f"{p.name} → {cp.tally_stock_item_name}")
            if not dry_run:
                before = {"name": p.name, "code": p.code}
                from app.modules.masters.tally_products import _product_code

                p.name, p.code, cp.source = cp.tally_stock_item_name, _product_code(cp.tally_stock_item_name), "TALLY"
                db.flush()
                write_audit(db, action="product.sample_converted_to_tally", entity_type="products", entity_id=p.id,
                            actor_id=None, company_id=company.id, before=before, after={"name": p.name, "code": p.code})
            continue
        if rates:
            report["kept_in_use"].append(f"{p.name} ({rates} customer rate(s)); switched off")
            if not dry_run:
                cp.erp_enabled = False
            continue
        report["removed"].append(p.name)
        if not dry_run:
            db.delete(cp)
            db.flush()
            write_audit(db, action="product.sample_removed", entity_type="products", entity_id=p.id, actor_id=None,
                        company_id=company.id, before={"name": p.name, "code": p.code, "tally": cp.tally_stock_item_name})
    if not dry_run:
        # drop product rows no company uses any more (sample catalog rows), with their units and own conversions
        orphans = db.execute(
            select(Product).where(~Product.id.in_(select(CompanyProduct.product_id)),
                                  Product.code.in_([*SAMPLE_CODES]))
        ).scalars().all()
        for p in orphans:
            db.execute(delete(ProductConversion).where(ProductConversion.product_id == p.id))
            db.delete(p)
        db.flush()
    return report


SAMPLE_CODES = ["AMB-PPC", "AMB-KAWACH", "AMB-PLUS", "SHR-ROC", "SHR-OPC", "SHR-ROOFON",
                *[f"KAM-FE550D-{d}" for d in (8, 10, 12, 16, 20, 25, 32)]]


def tally_stock(db: Session, tally_url: str | None, save: bool) -> None:
    import time

    from app.modules.integrations.tally.sync import normalize_url
    from app.modules.purchases import service, stock, tally_io

    company = db.execute(select(Company).where(Company.is_demo.is_(False)).order_by(Company.created_at)).scalars().first()
    cfg = service.load_cfg(db, company.id)
    url = normalize_url(tally_url) if tally_url else service.tally_url(db, company.id, cfg)
    t0 = time.time()
    data = tally_io.Tally(url, company.tally_company_name or cfg["company"]).godown_stock()
    print(f"Tally's Godown Summary up to {data['to']}, read in {time.time() - t0:.0f}s"
          + (f"; notes: {data['skipped']}" if data["skipped"] else ""))
    last = None
    for (g, item), q in sorted(data["stock"].items(), key=lambda x: (x[0][0].lower(), x[0][1].lower())):
        if q == 0:
            continue
        if g != last:
            print(f"\n{g}")
            last = g
        print(f"  {item:<40} {q:>12,.3f} {data['units'].get(item, '')}")
    if save:
        snap, _ = stock.start(db, company.id, "cli", None)
        stock.run(snap.id)
        print("\nSaved in the ERP.")


def tally_stock_probe(db: Session, godown: str, item: str, tally_url: str | None, out: str) -> None:
    """Try the ways Tally can report stock per godown itself; save every reply. Each try gives up after 90 s."""
    from datetime import date
    from pathlib import Path
    from xml.sax.saxutils import escape as e

    from app.modules.integrations.tally.sync import normalize_url
    from app.modules.purchases import service, tally_io

    company = db.execute(select(Company).where(Company.is_demo.is_(False)).order_by(Company.created_at)).scalars().first()
    cfg = service.load_cfg(db, company.id)
    url = normalize_url(tally_url) if tally_url else service.tally_url(db, company.id, cfg)
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.glob("*"):
        f.unlink()
    t = tally_io.Tally(url, company.tally_company_name or cfg["company"], timeout=90)
    t.save_dir = folder
    co = e(t.company)
    fy = date(date.today().year - (1 if date.today().month < 4 else 0), 4, 1)
    sv = (f"<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT><SVCURRENTCOMPANY>{co}</SVCURRENTCOMPANY>"
          f"<SVFROMDATE>{fy:%Y%m%d}</SVFROMDATE><SVTODATE>{date.today():%Y%m%d}</SVTODATE>")

    def coll(name, body):
        return (f"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE>"
                f"<ID>{name}</ID></HEADER><BODY><DESC><STATICVARIABLES>{sv}</STATICVARIABLES><TDL><TDLMESSAGE>"
                f'<COLLECTION NAME="{name}" ISMODIFY="No">{body}</COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>')

    def report(name, extra):
        return (f"<ENVELOPE><HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER><BODY><EXPORTDATA><REQUESTDESC>"
                f"<REPORTNAME>{name}</REPORTNAME><STATICVARIABLES>{sv}{extra}</STATICVARIABLES></REQUESTDESC>"
                f"</EXPORTDATA></BODY></ENVELOPE>")

    g = e(godown)
    ex = "<EXPLODEFLAG>Yes</EXPLODEFLAG>"
    tries = {
        "H Godown Summary, detailed": report("Godown Summary", ex),
        "I Godown Summary, detailed + godown": report("Godown Summary", ex + f"<GODOWNNAME>{g}</GODOWNNAME>"),
        "J Stock Summary, detailed": report("Stock Summary", ex),
        "K Godown Summary, detailed, item-wise": report("Godown Summary", ex + "<ISITEMWISE>Yes</ISITEMWISE>"
                                                         "<DSPSHOWITEMS>Yes</DSPSHOWITEMS>"),
    }
    for label, xml in tries.items():
        try:
            root = t.post(xml)
            text = tally_io.ET.tostring(root, encoding="unicode")
            hits = [m for m in text.split("<") if item.upper() in m.upper()][:3]
            print(f"{label}: {len(text):,} chars · mentions {item}: {len(hits)}")
        except tally_io.TallyError as ex:
            print(f"{label}: failed — {ex}")
    print(f"\nSaved every reply in {folder.resolve()}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("seed-base", "seed-company", "seed-catalog", "seed-demo"):
        sub.add_parser(name)
    adm = sub.add_parser("create-admin")
    adm.add_argument("--phone", required=True)
    adm.add_argument("--name", required=True)
    rpw = sub.add_parser("reset-password", help="set a new password for a user who forgot it")
    rpw.add_argument("--phone", help="leave out to pick from the list of users")
    rsp = sub.add_parser("remove-sample-products")
    rsp.add_argument("--dry-run", action="store_true")
    tal = sub.add_parser("sync-tally-masters")
    tal.add_argument("--tally-url", required=True, help="e.g. http://100.64.0.5:9000")
    tal.add_argument("--dry-run", action="store_true", help="show what Tally has; save nothing")
    tal.add_argument("--save-xml", metavar="DIR", help="also save Tally's raw replies into DIR (for diagnosis)")
    stk = sub.add_parser("tally-stock", help="read godown-wise stock from Tally and print it (to compare with "
                                           "Tally's Godown Summary); --save stores it in the ERP")
    stk.add_argument("--tally-url", help="default: the address saved by the web Sync button")
    stk.add_argument("--save", action="store_true")
    prb = sub.add_parser("tally-stock-probe", help="ask Tally for godown-wise stock in several ways (quick, for diagnosis)")
    prb.add_argument("--godown", default="DIRECT")
    prb.add_argument("--item", default="SHREE ROC")
    prb.add_argument("--tally-url")
    prb.add_argument("--out", default="tally_debug")
    sub.add_parser("import-purchase-uploader", help="take over the Purchase Uploader app's settings and push log")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.cmd == "seed-base":
            seed_base(db)
        elif args.cmd == "seed-company":
            seed_company(db)
        elif args.cmd == "seed-catalog":
            seed_catalog(db)
        elif args.cmd == "seed-demo":
            seed_demo(db)
        elif args.cmd == "remove-sample-products":
            r = remove_sample_products(db, args.dry_run)
            print(("Would remove" if args.dry_run else "Removed"), len(r["removed"]), "sample products:")
            for n in r["removed"]:
                print("  -", n)
            for n in r["converted"]:
                print("  converted to the Tally item (customer rates kept):", n)
            for n in r["kept_in_use"]:
                print("  kept, not in Tally but has customer rates:", n)
            if not args.dry_run:
                print("Now run sync-tally-masters to bring products in from Tally.")
        elif args.cmd == "sync-tally-masters":
            sync_tally_masters(db, args.tally_url, args.dry_run, args.save_xml)
        elif args.cmd == "create-admin":
            create_admin(db, args.phone, args.name)
        elif args.cmd == "reset-password":
            reset_password(db, args.phone)
        elif args.cmd == "tally-stock":
            tally_stock(db, args.tally_url, args.save)
        elif args.cmd == "tally-stock-probe":
            tally_stock_probe(db, args.godown, args.item, args.tally_url, args.out)
        elif args.cmd == "import-purchase-uploader":
            from app.modules.purchases.service import import_from_uploader

            company = db.execute(select(Company).where(Company.is_demo.is_(False))
                                 .order_by(Company.created_at)).scalars().first()
            print(import_from_uploader(db, company))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
