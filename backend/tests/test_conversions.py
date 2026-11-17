from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.modules.masters.conversions import convert
from app.modules.masters.models import Brand, Product, ProductConversion


def _p(db, code):
    return db.execute(select(Product).where(Product.code == code)).scalar_one()


def test_cement_mt_to_bags_via_bag_weight(world, db):
    world.company()
    world.catalog()
    r = convert(db, _p(db, "SHR-ROC"), Decimal("25"), "MT")
    assert r.result == Decimal("500.000000") and r.to_uom == "BAG" and r.factor == Decimal("20")


def test_20kg_pack_gives_50_bags_per_mt(world, db):
    world.company()
    world.catalog()
    jsw = db.execute(select(Brand).where(Brand.code == "JSW")).scalar_one()
    p = Product(code="JSW-MICRO", name="JSW Microfine 20kg", brand_id=jsw.id, category="CEMENT", base_uom="BAG",
                bag_weight_kg=Decimal("20"))
    db.add(p)
    db.commit()
    assert convert(db, p, Decimal("1"), "MT").result == Decimal("50")


def test_tmt_pieces_to_kg_uses_chart_and_length(world, db):
    world.company()
    world.catalog()
    r = convert(db, _p(db, "KAM-FE550D-12"), Decimal("10"), "PC")
    assert r.result == Decimal("106.560000")  # 10 × 12 m × 0.888 kg/m
    assert convert(db, _p(db, "KAM-FE550D-12"), Decimal("2.5"), "MT").result == Decimal("2500")


def test_missing_conversion_is_an_error_not_a_guess(world, db, client):
    c = world.company()
    world.catalog()
    admin = world.user(c, ["SUPER_ADMIN"])
    pid = str(_p(db, "KAM-FE550D-12").id)
    r = client.post("/api/v1/products/convert", json={"product_id": pid, "qty": "3", "from_uom": "BDL"},
                    headers=world.headers(admin, c))
    assert r.status_code == 422 and r.json()["code"] == "CONVERSION_NOT_FOUND"


def test_product_specific_and_effective_dated_conversion_wins(world, db):
    world.company()
    world.catalog()
    p = _p(db, "KAM-FE550D-12")
    db.add(ProductConversion(product_id=p.id, from_uom="BDL", to_uom="PC", factor=Decimal("10")))
    # a newer chart from tomorrow must not apply today
    db.add(ProductConversion(product_id=p.id, from_uom="M", to_uom="KG", factor=Decimal("0.900"),
                             effective_from=date.today() + timedelta(days=1)))
    db.commit()
    assert convert(db, p, Decimal("1"), "BDL").result == Decimal("106.560000")
    assert convert(db, p, Decimal("1"), "BDL", on=date.today() + timedelta(days=1)).result == Decimal("108")


def test_conversion_api_round_trip(world, db, client):
    c = world.company()
    world.catalog()
    admin = world.user(c, ["SUPER_ADMIN"])
    pid = str(_p(db, "AMB-PPC").id)
    r = client.post("/api/v1/products/convert", json={"product_id": pid, "qty": "450", "from_uom": "BAG",
                                                      "to_uom": "MT"}, headers=world.headers(admin, c))
    assert r.status_code == 200
    assert Decimal(r.json()["result"]) == Decimal("22.5")
