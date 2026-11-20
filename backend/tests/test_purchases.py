"""Purchase upload (ported from the Purchase Uploader app), godown stock from Tally, and Direct trucks used for
delivery. Tally itself is replaced by a fake that records what would be sent."""

import io
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import select

from app.modules.purchases import service as psvc
from app.modules.purchases import stock as stock_svc
from app.modules.purchases import tally_io
from app.modules.purchases.models import DirectTruck, StockSnapshot
from tests.test_orders_dispatch import API, Shop

TODAY = date.today()


def ambuja_file(rows: list[tuple]) -> bytes:
    """An Ambuja 'Invoice details' export: (invoice, mt, gross, truck, material)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Invoice", "Ref No.", "Bill Date", "Gross Value", "Net Value", "Quantity", "Truck No.",
               "Material Description", "Delivery Doc", "Bill Type", "Sold To", "CGST", "SGST", "IGST"])
    for inv, mt, gross, truck, mat in rows:
        ws.append([inv, "R" + inv[-4:], TODAY.strftime("%d.%m.%Y"), gross, gross / 1.28, mt, truck, mat, "D1", "ZF2",
                   "", 0, 0, 0])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class FakeTally:
    """Stands in for TallyPrime. Class-level state so every instance shares it within a test."""

    existing: list = []
    imported: list = []
    stock: dict = {}

    def __init__(self, url, company="", timeout=60):
        self.url, self.company = url, company

    def masters(self):
        return {"ledgers": [{"name": "PURCHASE", "parent": "Purchase Accounts"},
                            {"name": "Ambuja Cements Ltd. (TT)", "parent": "Sundry Creditors"},
                            {"name": "Shree Cement Limited (A T Trading)", "parent": "Sundry Creditors"}],
                "items": [{"name": "Ambuja", "unit": "Bags"}, {"name": "Ambuja Plus", "unit": "Bags"},
                          {"name": "Ambuja Kawach", "unit": "Bags"}],
                "godowns": ["Direct", "Nasirpur", "Uttam Nagar"]}

    def existing_purchases(self, d_from, d_to, kind="Purchase"):
        return list(FakeTally.existing)

    def import_voucher(self, xml):
        FakeTally.imported.append(xml)
        return {"ok": True, "vchid": str(1000 + len(FakeTally.imported))}

    def godown_stock(self, to_date=None):
        return {"stock": dict(FakeTally.stock), "units": {"Ambuja": "Bags", "Ambuja Plus": "Bags"},
                "vouchers": 3, "skipped": {}, "from": "2026-04-01", "to": TODAY.isoformat()}


@pytest.fixture
def shop(world, monkeypatch):
    monkeypatch.setattr(tally_io, "Tally", FakeTally)
    monkeypatch.setattr(stock_svc, "PAUSED", "")
    monkeypatch.setattr(psvc, "UPLOADER_DIR", __import__("pathlib").Path("/nonexistent"))
    FakeTally.existing, FakeTally.imported, FakeTally.stock = [], [], {}
    psvc._MASTERS.clear()
    psvc._EXISTING.clear()
    s = Shop(world)
    s.direct = world.godown(s.co, "Direct")
    s.co.tally_company_name = "Ishika Traders Daily."
    from app.modules.platform.models import SystemSetting

    world.db.add(SystemSetting(company_id=s.co.id, key="tally.url", value={"v": "http://tally.test:9000"}))
    world.db.commit()
    s.accounts = world.user(s.co, ["ACCOUNTS_EXECUTIVE"])
    return s


def upload(shop, user, content: bytes, name="Ambuja 9-11.xlsx"):
    return shop.w.client.post(f"{API}/purchases/upload", headers=shop.h(user),
                              files=[("files", (name, content, "application/vnd.ms-excel"))])


def test_upload_check_and_push_like_the_app(shop):
    f = ambuja_file([("NJAM072611001474", 30, 154560.0, "HR55AB1234", "AMBUJA CEMENT PPC+ 50KG"),
                     ("NG60072611005456", 8, 58112.0, "DL01LAF4228", "AMBUJA CEMENT PPC 50KG")])
    r = upload(shop, shop.accounts, f)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["tally_checked"] is True and p["company"] == "Ishika Traders Daily."
    by = {i["invoice_no"]: i for i in p["invoices"]}
    direct, godown = by["NJAM072611001474"], by["NG60072611005456"]
    assert direct["godown"] == "Direct" and direct["direct"] is True and direct["bags"] == 600
    assert direct["lines"][0]["item"] == "Ambuja Plus" and direct["truck"] == "HR55AB1234"
    assert godown["godown"] == "Nasirpur" and godown["direct"] is False and godown["bags"] == 160
    assert direct["status"] == godown["status"] == "ready"

    # Exactly the app's voucher
    x = shop.w.client.get(f"{API}/purchases/xml", params={"id": direct["id"]}, headers=shop.h(shop.accounts)).json()["xml"]
    assert "<VOUCHERNUMBER>NJAM072611001474</VOUCHERNUMBER>" in x and "<GODOWNNAME>Direct</GODOWNNAME>" in x
    assert "<ACTUALQTY> 600 Bags</ACTUALQTY>" in x and "<LEDGERNAME>PURCHASE</LEDGERNAME>" in x

    r = shop.post(shop.accounts, "/purchases/push", {"ids": [direct["id"], godown["id"]]})
    assert r.status_code == 200, r.text
    assert [x["ok"] for x in r.json()["results"]] == [True, True]
    assert len(FakeTally.imported) == 2
    assert {i["status"] for i in r.json()["invoices"]} == {"posted"}

    # Pushing again does nothing: the ERP remembers what it pushed.
    r = shop.post(shop.accounts, "/purchases/push", {"ids": [direct["id"]]}).json()
    assert r["results"][0]["skipped"] is True and len(FakeTally.imported) == 2

    # The Direct invoice became a Direct truck with 600 bags of Ambuja Plus; the Nasirpur one did not.
    trucks = shop.w.client.get(f"{API}/direct-trucks", headers=shop.h(shop.ops)).json()
    assert len(trucks) == 1
    t = trucks[0]
    assert t["truck_no"] == "HR55AB1234" and t["invoice_no"] == "NJAM072611001474" and t["godown_name"] == "Direct"
    assert t["lines"][0]["tally_item"] == "Ambuja Plus" and t["lines"][0]["qty"] == "600.000"
    # ...and Tally's godown stock was re-read after the push
    assert shop.db.execute(select(StockSnapshot)).scalars().first() is not None


def test_already_in_tally_and_mapping_rules(shop):
    FakeTally.existing = [{"ref": "NJAM072611001474", "number": "NJAM072611001474", "party": "", "date": ""}]
    f = ambuja_file([("NJAM072611001474", 30, 154560.0, "HR55AB1234", "AMBUJA CEMENT PPC+ 50KG"),
                     ("NXYZ072611000001", 10, 50000.0, "HR55AB9999", "AMBUJA NEW BRAND 50KG")])
    p = upload(shop, shop.accounts, f).json()
    by = {i["invoice_no"]: i for i in p["invoices"]}
    assert by["NJAM072611001474"]["status"] == "exists"
    new = by["NXYZ072611000001"]
    assert new["status"] == "problem"
    kinds = {(u["kind"], u["text"]) for u in p["unmapped"]}
    assert ("product", "AMBUJA NEW BRAND 50KG") in kinds and ("godown", "NXYZ") in kinds

    # Save rules once; they apply to this and every later file.
    shop.post(shop.accounts, "/purchases/map", {"kind": "product", "supplier": "ambuja",
                                                "text": "AMBUJA NEW BRAND 50KG", "value": "Ambuja Kawach"})
    p = shop.post(shop.accounts, "/purchases/map", {"kind": "godown", "supplier": "ambuja", "text": "NXYZ",
                                                    "value": "Uttam Nagar"}).json()
    new = next(i for i in p["invoices"] if i["invoice_no"] == "NXYZ072611000001")
    assert new["status"] == "ready" and new["godown"] == "Uttam Nagar" and new["lines"][0]["item"] == "Ambuja Kawach"

    # A Direct invoice already in Tally can still be added to the Direct trucks list.
    r = shop.post(shop.accounts, "/purchases/direct-truck", {"id": "ambuja:NJAM072611001474"})
    assert r.status_code == 201, r.text
    # Sales cannot use the purchase upload.
    assert upload(shop, shop.sales, f).status_code == 403


def test_godown_stock_and_dispatch_options(shop):
    FakeTally.stock = {("Nasirpur", "Ambuja"): Decimal("500"), ("Uttam Nagar", "Ambuja"): Decimal("100"),
                       ("Direct", "Ambuja"): Decimal("0"), ("Old Shed", "Ambuja"): Decimal("7")}
    r = shop.post(shop.ops, "/stock/refresh")
    assert r.status_code == 202
    st = shop.w.client.get(f"{API}/stock", headers=shop.h(shop.ops)).json()
    assert st["latest"]["status"] == "SUCCESS" and st["as_of"]
    names = {g["name"]: g for g in st["godowns"]}
    assert names["Nasirpur"]["items"][0]["qty"] == "500.000" and names["Nasirpur"]["items"][0]["product_name"]
    assert "Old Shed" in names and names["Old Shed"]["godown_id"] is None  # in Tally only

    o = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags='400').json()['id']}/approve", {}).json()
    opt = shop.w.client.get(f"{API}/orders/{o['id']}/dispatch-options", headers=shop.h(shop.ops)).json()
    gd = [g["name"] for g in opt["godowns"]]
    assert gd[0] == "Nasirpur" and "Direct" in gd  # enough stock first; Direct is offered too
    nas = opt["godowns"][0]
    assert nas["enough"] is True and nas["lines"][0]["available_qty"] == "500.000"
    utt = next(g for g in opt["godowns"] if g["name"] == "Uttam Nagar")
    assert utt["enough"] is False and utt["has_stock"] is True

    # Planning 300 bags at Nasirpur reserves them until the truck leaves.
    assert shop.plan(shop.ops, o, "300").status_code == 201
    opt = shop.w.client.get(f"{API}/orders/{o['id']}/dispatch-options", headers=shop.h(shop.ops)).json()
    nas = next(g for g in opt["godowns"] if g["name"] == "Nasirpur")
    assert nas["lines"][0]["reserved_qty"] == "300.000" and nas["lines"][0]["available_qty"] == "200.000"
    assert opt["lines"][0]["remaining_qty"] == "100.000"


def _direct_truck(shop, bags=600):
    f = ambuja_file([("NJAM072611001474", bags / 20, 154560.0, "HR55AB1234", "AMBUJA CEMENT PPC 50KG")])
    inv = upload(shop, shop.accounts, f).json()["invoices"][0]
    shop.post(shop.accounts, "/purchases/push", {"ids": [inv["id"]]})
    return shop.db.execute(select(DirectTruck)).scalar_one()


def test_deliver_straight_from_a_direct_truck(shop):
    truck = _direct_truck(shop)
    o = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags='400').json()['id']}/approve", {}).json()
    opt = shop.w.client.get(f"{API}/orders/{o['id']}/dispatch-options", headers=shop.h(shop.ops)).json()
    t = opt["direct_trucks"][0]
    assert t["truck_no"] == "HR55AB1234" and t["matches"] is True and t["lines"][0]["left_qty"] == "600.000"

    line = o["items"][0]["id"]
    body = {"sales_order_id": o["id"], "direct_truck_id": str(truck.id),
            "lines": [{"order_item_id": line, "qty": "700"}]}
    assert shop.post(shop.ops, "/delivery-orders", body).json()["code"] in ("MORE_THAN_ON_TRUCK", "QTY_EXCEEDS_ORDER")
    body["lines"][0]["qty"] = "400"
    r = shop.post(shop.ops, "/delivery-orders", body)
    assert r.status_code == 201, r.text
    do = r.json()
    # Straight to delivery: sent out at once, no driver or loading needed, invoice as the supplier reference.
    assert do["status"] == "DISPATCHED" and do["source"] == "DIRECT" and do["vehicle_no"] == "HR55AB1234"
    assert do["godown_name"] == "Direct" and do["supplier_ref"] == "NJAM072611001474" and "deliver" in do["can"]
    assert shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()["status"] == "DISPATCHED"
    left = shop.w.client.get(f"{API}/direct-trucks", headers=shop.h(shop.ops)).json()[0]["lines"][0]["left_qty"]
    assert left == "200.000"

    # A wrong direct delivery can be undone until delivery is recorded.
    r = shop.post(shop.ops, f"/delivery-orders/{do['id']}/cancel", {"reason": "wrong dealer"})
    assert r.status_code == 200, r.text
    assert shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()["status"] == "APPROVED"

    # Deliver the whole truck: 400 to this dealer + 200 to another → truck closes by itself.
    do = shop.post(shop.ops, "/delivery-orders", body).json()
    r = shop.post(shop.ops, f"/delivery-orders/{do['id']}/deliver", {"recipient_name": "Sharma"})
    assert r.status_code == 200 and r.json()["status"] == "DELIVERED"
    o2 = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags='200').json()['id']}/approve", {}).json()
    r = shop.post(shop.ops, "/delivery-orders", {"sales_order_id": o2["id"], "direct_truck_id": str(truck.id),
                                                 "lines": [{"order_item_id": o2["items"][0]["id"], "qty": "200"}]})
    assert r.status_code == 201, r.text
    shop.db.expire_all()
    assert shop.db.get(DirectTruck, truck.id).status == "CLOSED"
    assert shop.w.client.get(f"{API}/direct-trucks", headers=shop.h(shop.ops)).json() == []


def test_send_out_own_truck_in_one_step(shop):
    o = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags='300').json()['id']}/approve", {}).json()
    r = shop.plan(shop.ops, o, "300", send_now=True)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "DISPATCHED" and r.json()["source"] == "OWN"
    # without a driver it is refused, as at the gate
    o = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags='100').json()['id']}/approve", {}).json()
    r = shop.plan(shop.ops, o, "100", send_now=True, driver_id=None)
    assert r.status_code == 422 and r.json()["code"] == "DRIVER_REQUIRED"


def test_stock_reader_uses_tallys_godown_summary():
    """The detailed Godown Summary is a flat list: each godown, then its items. Built from Tally's real reply of
    15 Nov 2026 (DIRECT and Nasirpur match the Tally screen)."""
    def row(name, qty=""):
        return (f"<DSPACCNAME><DSPDISPNAME>{name}</DSPDISPNAME></DSPACCNAME>"
                f"<DSPSTKINFO><DSPSTKCL><DSPCLQTY>{qty}</DSPCLQTY><DSPCLRATE></DSPCLRATE><DSPCLAMTA></DSPCLAMTA>"
                "</DSPSTKCL></DSPSTKINFO>")

    report = ET.fromstring("<ENVELOPE>" + "".join([
        row("DIRECT", "-2417 Bag"), row("Ambuja", "220 Bag"), row("Ambuja Kawach"), row("Ambuja Plus", "10 Bag"),
        row("ROOFON PPC", "-731 Bag"), row("SHREE OPC", "-35 Bag"), row("SHREE ROC", "-1881 Bag"),
        row("DL01MA5915"), row("SHREE ROC"),  # a truck-number godown with nothing in it
        row("Main Location"), row("Ambuja", "-570 Bag"), row("Steel", "-6683.58 KGS"),
        row("Nasirpur", "-489 Bag"), row("ROOFON PPC", "-675 Bag"), row("SHREE ROC", "186 Bag"),
    ]) + "</ENVELOPE>")
    masters = {
        "Godown": ET.fromstring('<E><GODOWN NAME="DIRECT"/><GODOWN NAME="DL01MA5915"/><GODOWN NAME="Main Location"/>'
                                '<GODOWN NAME="Nasirpur"/></E>'),
        "StockItem": ET.fromstring("<E>" + "".join(f'<STOCKITEM NAME="{n}"><BASEUNITS>{u}</BASEUNITS></STOCKITEM>' for n, u in [
            ("Ambuja", "Bag"), ("Ambuja Kawach", "Bag"), ("Ambuja Plus", "Bag"), ("ROOFON PPC", "Bag"),
            ("SHREE OPC", "Bag"), ("SHREE ROC", "Bag"), ("Steel", "KGS")]) + "</E>"),
    }
    t = tally_io.Tally("http://x:9000", "Ishika Traders Daily.")
    t._collection = lambda ctype, *a, **k: masters[ctype]
    sent = []
    t.post = lambda xml, timeout=None: (sent.append(xml), report)[1]
    out = t.godown_stock(date(2026, 11, 15))
    s = out["stock"]
    assert s[("DIRECT", "Ambuja")] == 220 and s[("DIRECT", "SHREE ROC")] == -1881 and s[("DIRECT", "ROOFON PPC")] == -731
    assert sum(q for (g, _), q in s.items() if g == "DIRECT") == -2417  # = Tally's DIRECT total
    assert s[("Nasirpur", "ROOFON PPC")] == -675 and s[("Nasirpur", "SHREE ROC")] == 186
    assert s[("Main Location", "Ambuja")] == -570 and ("DL01MA5915", "SHREE ROC") not in s
    assert out["units"]["Steel"] == "KGS" and out["skipped"] == {}
    assert "<REPORTNAME>Godown Summary</REPORTNAME>" in sent[0] and "<EXPLODEFLAG>Yes</EXPLODEFLAG>" in sent[0]


def test_direct_godown_is_offered_and_takes_a_truck_number(shop):
    """No Direct truck on the list yet (purchase entered outside the ERP): pick the Direct godown and type the
    factory truck's number; it goes straight to delivery like any direct truck."""
    o = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags='250').json()['id']}/approve", {}).json()
    opt = shop.w.client.get(f"{API}/orders/{o['id']}/dispatch-options", headers=shop.h(shop.ops)).json()
    direct = next(g for g in opt["godowns"] if g["name"] == "Direct")
    assert direct["kind"] == "DIRECT"
    body = {"sales_order_id": o["id"], "godown_id": direct["godown_id"], "direct_truck_no": "hr55 ab 1234",
            "supplier_ref": "DL2601043214", "lines": [{"order_item_id": o["items"][0]["id"], "qty": "250"}]}
    r = shop.post(shop.ops, "/delivery-orders", body)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "DISPATCHED" and d["source"] == "DIRECT" and d["vehicle_no"] == "HR55AB1234"
    assert d["supplier_ref"] == "DL2601043214" and d["godown_name"] == "Direct"
    # its load is exactly this delivery, so it is done; a number on another godown is refused
    assert shop.w.client.get(f"{API}/direct-trucks", headers=shop.h(shop.ops)).json() == []
    body["godown_id"] = str(shop.g1.id)
    assert shop.post(shop.ops, "/delivery-orders", body).status_code == 422


def test_typed_factory_truck_numbers_are_offered_again(shop):
    """A factory truck typed once is suggested next time (most recent first), matched on any part of it."""
    opt = None
    for bags, truck in (("50", "hr55 ab 1234"), ("60", "UP14CD5678"), ("40", "HR55AB1234")):
        o = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags=bags).json()['id']}/approve", {}).json()
        opt = opt or shop.w.client.get(f"{API}/orders/{o['id']}/dispatch-options", headers=shop.h(shop.ops)).json()
        direct = next(g for g in opt["godowns"] if g["name"] == "Direct")
        r = shop.post(shop.ops, "/delivery-orders", {
            "sales_order_id": o["id"], "godown_id": direct["godown_id"], "direct_truck_no": truck,
            "lines": [{"order_item_id": o["items"][0]["id"], "qty": bags}]})
        assert r.status_code == 201, r.text
    get = lambda q="": shop.w.client.get(f"{API}/dispatch/factory-truck-numbers?q={q}",  # noqa: E731
                                         headers=shop.h(shop.ops)).json()
    allt = get()
    assert [t["truck_no"] for t in allt] == ["HR55AB1234", "UP14CD5678"] and allt[0]["times"] == 2
    assert [t["truck_no"] for t in get("1234")] == ["HR55AB1234"]
    assert [t["truck_no"] for t in get("up 14")] == ["UP14CD5678"]
    assert shop.w.client.get(f"{API}/dispatch/factory-truck-numbers", headers=shop.h(shop.sales)).status_code == 403
