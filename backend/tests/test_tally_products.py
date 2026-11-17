from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.modules.integrations.tally.xml_client import parse_stock_items
from app.modules.masters.models import CompanyProduct, Product
from app.modules.masters.schemas import TallyStockItemIn
from app.modules.masters.tally_products import classify, upsert_products_from_tally

ITEMS = parse_stock_items((Path(__file__).parent / "fixtures" / "tally" / "stockitems.xml").read_text())


def _by_name(db, company_id):
    rows = db.execute(select(Product, CompanyProduct).join(CompanyProduct, CompanyProduct.product_id == Product.id)
                      .where(CompanyProduct.company_id == company_id)).all()
    return {p.name: (p, cp) for p, cp in rows}


def test_classify_reads_brand_category_and_size_from_names():
    c = {i.name: classify(i) for i in ITEMS}
    assert c["Ambuja"]["category"] == "CEMENT" and c["Ambuja"]["brand"][0] == "AMBUJA"
    assert c["Shree ROC"]["brand"][0] == "SHREE" and c["Shree ROC"]["bag_weight_kg"] == 50
    assert c["Rockstrong PPC"]["brand"][0] == "ROCKSTRONG" and c["Rockstrong PPC"]["cement_grade"] == "PPC"
    assert c["Kamdhenu 12mm Fe550D"]["diameter_mm"] == 12 and c["Kamdhenu 12mm Fe550D"]["tmt_grade"] == "Fe550D"
    assert c["Kamdhenu 12mm Fe550D"]["base_uom"] == "KG"  # TMT always stocked in kg, whatever Tally uses
    assert c["Sariya 8 mm"]["category"] == "TMT" and c["Sariya 8 mm"]["brand"] is None
    assert c["Binding Wire"]["category"] == "GENERAL" and c["Binding Wire"]["base_uom"] == "KG"
    assert c["TMT Bar"]["category"] == "OTHER"  # TMT with no size → needs setup
    assert classify(TallyStockItemIn(name="JSW Microfine 20kg", base_unit="Bag"))["bag_weight_kg"] == 20


def test_import_switches_on_only_what_is_fully_understood(world, db):
    c = world.company()
    stats = upsert_products_from_tally(db, c.id, ITEMS, None, full=True)
    db.commit()
    assert stats["created"] == 8 and stats["needs_setup"] == 2  # Sariya (no brand), TMT Bar (no size)
    rows = _by_name(db, c.id)
    assert rows["Ambuja"][1].erp_enabled and rows["Kamdhenu 12mm Fe550D"][1].erp_enabled
    # other material: fully set up, but held off until the next phase
    assert not rows["Binding Wire"][1].erp_enabled and rows["Binding Wire"][0].missing_setup() == []
    assert rows["Sariya 8 mm"][0].missing_setup() == ["brand"]
    # re-sync changes nothing
    again = upsert_products_from_tally(db, c.id, ITEMS, None, full=True)
    assert again["created"] == 0 and again["unchanged"] == 8


def test_seeded_products_link_by_tally_name_instead_of_duplicating(world, db):
    c = world.company()
    world.catalog(c)  # sample products mapped to Tally names "Ambuja", "Shree ROC", …
    before = db.execute(select(Product)).scalars().all().__len__()
    stats = upsert_products_from_tally(db, c.id, ITEMS[:2], None)
    db.commit()
    assert stats["linked"] == 2 and stats["created"] == 0
    assert len(db.execute(select(Product)).scalars().all()) == before


def test_switch_setup_and_deletion_via_api(world, db, client):
    c = world.company()
    upsert_products_from_tally(db, c.id, ITEMS, None)
    db.commit()
    rows = _by_name(db, c.id)
    h = world.headers(world.user(c, ["SUPER_ADMIN"]), c)
    sariya = str(rows["Sariya 8 mm"][0].id)
    r = client.post(f"/api/v1/products/{sariya}/erp-enable", headers=h)
    assert r.status_code == 422 and r.json()["code"] == "PRODUCT_NEEDS_SETUP"

    page = client.get("/api/v1/products", params={"needs_setup": True}, headers=h).json()
    assert {i["name"] for i in page["items"]} == {"Sariya 8 mm", "TMT Bar"}

    # finish setup of "TMT Bar": category TMT needs a diameter
    tmt_bar = str(rows["TMT Bar"][0].id)
    kam = rows["Kamdhenu 12mm Fe550D"][0].brand_id
    r = client.patch(f"/api/v1/products/{tmt_bar}", json={"category": "TMT", "brand_id": str(kam)}, headers=h)
    assert r.status_code == 422
    r = client.patch(f"/api/v1/products/{tmt_bar}", json={"category": "TMT", "brand_id": str(kam), "diameter_mm": 16},
                     headers=h)
    assert r.status_code == 200 and r.json()["setup_missing"] == [] and r.json()["base_uom"] == "KG"
    assert {u["uom"] for u in r.json()["units"]} == {"KG", "MT", "PC", "BDL"}
    assert client.post(f"/api/v1/products/{tmt_bar}/erp-enable", headers=h).json()["erp_enabled"] is True

    # switch off; a re-sync keeps it off
    amb = str(rows["Ambuja"][0].id)
    assert client.post(f"/api/v1/products/{amb}/erp-disable", headers=h).json()["erp_enabled"] is False
    upsert_products_from_tally(db, c.id, ITEMS, None)
    db.commit()
    assert client.get(f"/api/v1/products/{amb}", headers=h).json()["erp_enabled"] is False

    # removed from Tally → flagged, switched off, hidden by default
    upsert_products_from_tally(db, c.id, [i for i in ITEMS if i.name != "Shree ROC"], None, full=True)
    db.commit()
    names = {i["name"] for i in client.get("/api/v1/products", params={"page_size": 200}, headers=h).json()["items"]}
    assert "Shree ROC" not in names
    assert client.post("/api/v1/products", json={"name": "x"}, headers=h).status_code == 405


def test_products_are_company_scoped(world, db, client):
    a, b = world.company("AAA"), world.company("BBB")
    upsert_products_from_tally(db, a.id, ITEMS[:1], None)
    db.commit()
    pid = str(_by_name(db, a.id)["Ambuja"][0].id)
    hb = world.headers(world.user(b, ["SUPER_ADMIN"]), b)
    assert client.get("/api/v1/products", headers=hb).json()["total"] == 0
    assert client.get(f"/api/v1/products/{pid}", headers=hb).status_code == 404


def test_rules_against_real_ishika_stock_items():
    """Names from the Ishika Traders Daily stock item list (2026-11-09)."""
    def c(name, unit="Bag", parent="Primary"):
        return classify(TallyStockItemIn(name=name, parent=parent, base_unit=unit))

    for name in ["Ambuja", "Ambuja Kawach", "SHREE ROC", "BANGUR MEGNA", "JK Cement", "Lafarge OPC", "WONDER PPC",
                 "Rockstrong OPC", "ULTRATECH SUPER", "JK LAKSHMI OPC"]:
        assert c(name)["category"] == "CEMENT" and c(name)["brand"] is not None, name
    for name, unit, uom in [("Ambuja Blockol", "Bag", "BAG"), ("Ambuja Cool Wall", "Pcs", "PC"),
                            ("Chemical", "Bag", "BAG"), ("Tile Adhesive", "Bag", "BAG"), ("Pop 25kg", "Bag", "BAG"),
                            ("AAC Block", "Cum", "CUM"), ("Bricks", "Pcs", "PC"), ("Zero M 10 Litre", "Litre", "LTR"),
                            ("Binding Wires", "KGS", "KG"), ("Dust (Rickshaw)", "rickshaw", "RKS"), ("Reta", "Ft", "FT"),
                            ("Cover Block", "PKT", "PKT"), ("Sak Distemper (10kg)", "NOS", "NOS")]:
        r = c(name, unit, "Building Material")
        assert (r["category"], r["base_uom"]) == ("GENERAL", uom), name
    assert c("TMT BARS", "KGS", "Steel")["category"] == "OTHER"  # TMT without a size: needs setup
    for code, brand in [("KD", "KAMDHENU"), ("JD", "JINDAL"), ("BL", "BIRLA"), ("R", "RUNGTA"), ("RT", "RATHI"),
                        ("SS", "SHYAM")]:
        r = c(f"TMT 12MM ({code})", "KGS", "Steel")
        assert r["brand"][0] == brand and r["diameter_mm"] == 12, code
    assert c("TMT 08MM(R)", "KGS", "Steel")["brand"][0] == "RUNGTA"
    assert c("TMT 08MM(R)", "KGS", "Steel")["diameter_mm"] == 8
    assert c("TMT 25MM (RT)", "KGS", "Steel")["diameter_mm"] == 25
    assert c("White Cement (25kg)")["bag_weight_kg"] == 25



def test_other_materials_locked_until_next_phase(world, db, client):
    c = world.company()
    items = [TallyStockItemIn(guid="b1", name="Bricks", parent="Building Material", base_unit="Pcs"),
             TallyStockItemIn(guid="b2", name="Rodi (Rickshaw)", parent="Building Material", base_unit="rickshaw")]
    upsert_products_from_tally(db, c.id, items, None)
    db.commit()
    h = world.headers(world.user(c, ["SUPER_ADMIN"]), c)
    bricks = str(_by_name(db, c.id)["Bricks"][0].id)
    got = client.get(f"/api/v1/products/{bricks}", headers=h).json()
    assert got["category"] == "GENERAL" and got["base_uom"] == "PC" and got["erp_enabled"] is False
    assert got["enable_blocked_reason"] == "Other materials go live in the next phase"
    r = client.post(f"/api/v1/products/{bricks}/erp-enable", headers=h)
    assert r.status_code == 422 and r.json()["code"] == "PRODUCT_NEXT_PHASE"
    # next phase: the setting is switched on, then products can be enabled
    assert client.put("/api/v1/settings/products.other_materials_enabled", json={"value": True}, headers=h).status_code == 200
    assert client.post(f"/api/v1/products/{bricks}/erp-enable", headers=h).json()["erp_enabled"] is True


def test_tmt_chart_applies_to_every_brand(world, db):
    from app.cli import seed_catalog
    from app.modules.masters.conversions import convert

    c = world.company()
    seed_catalog(db)
    upsert_products_from_tally(db, c.id, [TallyStockItemIn(guid="t", name="TMT 12MM (JD)", parent="Steel",
                                                           base_unit="KGS")], None)
    db.commit()
    p = _by_name(db, c.id)["TMT 12MM (JD)"][0]
    assert p.brand.code == "JINDAL"
    assert convert(db, p, 10, "PC").result == Decimal("106.56")  # 10 × 12 m × 0.888 kg/m (IS 1786 chart)


def test_remove_sample_products_leaves_only_tally_items(world, db, client):
    from sqlalchemy import update

    from app.cli import COMPANY_CODE, remove_sample_products, seed_company, seed_sample_products
    from app.modules.masters.models import Company

    seed_company(db)
    company = db.execute(select(Company).where(Company.code == COMPANY_CODE)).scalar_one()
    seed_sample_products(db, company)  # what the early seed-catalog created for the real company
    db.execute(update(CompanyProduct).where(CompanyProduct.company_id == company.id).values(source="SEED"))
    db.commit()
    # first sync linked some samples to Tally by name ("Ambuja" → sample "Ambuja PPC")
    upsert_products_from_tally(db, company.id, ITEMS, None, full=True)
    db.commit()
    assert "Ambuja PPC" in _by_name(db, company.id)

    preview = remove_sample_products(db, dry_run=True)
    assert len(preview["removed"]) == 13 and _by_name(db, company.id)["Ambuja PPC"]

    report = remove_sample_products(db)
    db.commit()
    assert len(report["removed"]) == 13 and report["kept_in_use"] == []
    upsert_products_from_tally(db, company.id, ITEMS, None, full=True)
    db.commit()
    rows = _by_name(db, company.id)
    assert set(rows) == {i.name for i in ITEMS}  # exactly Tally's items, under Tally's names
    assert all(cp.source == "TALLY" for _, cp in rows.values())
    assert db.execute(select(Product).where(Product.code == "AMB-PPC")).first() is None


def test_remove_sample_products_keeps_ones_with_rates(world, db):
    from datetime import date

    from sqlalchemy import update

    from app.cli import COMPANY_CODE, remove_sample_products, seed_company, seed_sample_products
    from app.modules.masters.models import Company, DealerRate
    from app.modules.masters.schemas import TallyLedgerIn
    from app.modules.masters.service import upsert_dealers_from_tally

    seed_company(db)
    company = db.execute(select(Company).where(Company.code == COMPANY_CODE)).scalar_one()
    seed_sample_products(db, company)
    db.execute(update(CompanyProduct).where(CompanyProduct.company_id == company.id).values(source="SEED"))
    upsert_dealers_from_tally(db, company.id, [TallyLedgerIn(guid="d", name="Dealer")], None)
    db.flush()
    from app.modules.masters.models import Dealer

    dealer = db.execute(select(Dealer)).scalar_one()
    ppc = db.execute(select(Product).where(Product.code == "AMB-PPC")).scalar_one()
    db.add(DealerRate(company_id=company.id, dealer_id=dealer.id, product_id=ppc.id, uom="BAG", rate=390,
                      valid_from=date.today()))
    db.commit()
    report = remove_sample_products(db)
    db.commit()
    # not linked to Tally yet + has a rate → kept but switched off
    assert len(report["removed"]) == 12 and report["kept_in_use"] == ["Ambuja PPC (1 customer rate(s)); switched off"]


def test_sample_with_rates_linked_to_tally_becomes_the_tally_item(world, db):
    from datetime import date

    from sqlalchemy import update

    from app.cli import COMPANY_CODE, remove_sample_products, seed_company, seed_sample_products
    from app.modules.masters.models import Company, Dealer, DealerRate
    from app.modules.masters.schemas import TallyLedgerIn
    from app.modules.masters.service import upsert_dealers_from_tally

    seed_company(db)
    company = db.execute(select(Company).where(Company.code == COMPANY_CODE)).scalar_one()
    seed_sample_products(db, company)
    db.execute(update(CompanyProduct).where(CompanyProduct.company_id == company.id).values(source="SEED"))
    upsert_products_from_tally(db, company.id, ITEMS, None, full=True)  # links sample "Ambuja PPC" ↔ Tally "Ambuja"
    upsert_dealers_from_tally(db, company.id, [TallyLedgerIn(guid="d", name="Dealer")], None)
    db.flush()
    ppc = db.execute(select(Product).where(Product.code == "AMB-PPC")).scalar_one()
    db.add(DealerRate(company_id=company.id, dealer_id=db.execute(select(Dealer)).scalar_one().id,
                      product_id=ppc.id, uom="BAG", rate=390, valid_from=date.today()))
    db.commit()
    report = remove_sample_products(db)
    upsert_products_from_tally(db, company.id, ITEMS, None, full=True)
    db.commit()
    assert report["converted"] == ["Ambuja PPC → Ambuja"]
    rows = _by_name(db, company.id)
    assert set(rows) == {i.name for i in ITEMS}  # no duplicate "Ambuja", no sample names
    assert rows["Ambuja"][0].id == ppc.id  # same product, so the customer rate still applies
