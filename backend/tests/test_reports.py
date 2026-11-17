"""Sales report, dealer statement from Tally, repeat last order, booking sent twice from a weak network,
one-box search and the order's history."""

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

from app.modules.platform.models import SystemSetting
from app.modules.purchases import service as psvc
from app.modules.purchases import tally_io
from tests.test_notifications import deliver
from tests.test_orders_dispatch import API, Shop


def get(shop, user, path, **params):
    return shop.w.client.get(f"{API}{path}", params=params, headers=shop.h(user))


def booked_and_delivered(shop, bags="300"):
    o = shop.order(shop.sales, bags=bags).json()
    shop.post(shop.ops, f"/orders/{o['id']}/approve")
    deliver(shop, o)
    return o


def test_sales_report_groups_and_excel(world):
    shop = Shop(world)
    booked_and_delivered(shop, "300")
    booked_and_delivered(shop, "200")
    shop.order(shop.sales, bags="100")  # booked, not delivered: not a sale yet
    r = get(shop, shop.owner, "/reports/sales", group="dealer").json()
    assert r["total"]["bags"] == "500" and r["total"]["amount"] == "175000.00" and r["total"]["trucks"] == 2
    assert r["rows"][0]["label"] == "Sharma Building Material" and r["rows"][0]["orders"] == 2
    assert r["total"]["tonnes"] == "25.00"  # 500 bags × 50 kg
    for g in ("day", "product", "salesperson", "godown"):
        assert get(shop, shop.owner, "/reports/sales", group=g).json()["rows"][0]["bags"] == "500"
    assert get(shop, shop.owner, "/reports/sales", group="salesperson").json()["rows"][0]["label"] == "Suresh"
    assert r["previous"]["bags"] == "0"
    x = get(shop, shop.owner, "/reports/sales", group="product", format="xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert get(shop, shop.sales, "/reports/sales").status_code == 403  # sales people have no reports


def test_repeat_last_order(world):
    shop = Shop(world)
    assert get(shop, shop.sales, "/orders/last", dealer_id=str(shop.dealer.id)).json() is None
    o = shop.order(shop.sales, bags="250", site_name="Rohini plot 7").json()
    last = get(shop, shop.sales, "/orders/last", dealer_id=str(shop.dealer.id)).json()
    assert last["order_no"] == o["order_no"] and last["site_name"] == "Rohini plot 7"
    assert last["lines"][0]["product_name"] == "Ambuja PPC" and Decimal(last["lines"][0]["qty"]) == 250
    assert last["lines"][0]["uom"] == "BAG" and last["lines"][0]["godown_name"] == "Nasirpur"


def test_order_sent_twice_from_a_weak_network_is_made_once(world):
    shop = Shop(world)
    a = shop.order(shop.sales, bags="120", client_ref="phone-7f3a9c21-0001")
    b = shop.order(shop.sales, bags="120", client_ref="phone-7f3a9c21-0001")
    assert a.status_code == 201 and b.status_code == 201 and a.json()["id"] == b.json()["id"]
    # a different booking is a different order (the usual duplicate check still applies)
    c = shop.order(shop.sales, bags="130", client_ref="phone-7f3a9c21-0002")
    assert c.json()["id"] != a.json()["id"]


def test_search_finds_orders_dealers_and_trucks(world):
    shop = Shop(world)
    o = booked_and_delivered(shop)
    shop.dealer.tally_mobile = "9810012345"
    shop.db.commit()
    r = get(shop, shop.ops, "/search", q=o["order_no"]).json()
    assert [x["id"] for x in r["orders"]] == [o["id"]]
    assert get(shop, shop.ops, "/search", q="sharma").json()["dealers"][0]["name"] == "Sharma Building Material"
    assert get(shop, shop.ops, "/search", q="98100 12345").json()["dealers"][0]["name"] == "Sharma Building Material"
    t = get(shop, shop.ops, "/search", q="dl 1la 1234").json()["trucks"]
    assert t and t[0]["vehicle"] == "DL1LA1234"
    # sales people see only their dealers' orders and no trucks
    s = get(shop, shop.sales, "/search", q="gupta").json()
    assert s["dealers"] == [] and s["trucks"] == []
    assert get(shop, shop.sales, "/search", q="x").status_code == 422


def test_order_history(world):
    shop = Shop(world)
    o = booked_and_delivered(shop)
    h = get(shop, shop.sales, f"/orders/{o['id']}/history").json()
    whats = [x["what"] for x in h]
    assert whats[0] == "Created" and "Create" not in whats and "Booked" in whats and "Approved" in whats
    assert any(w.startswith("Truck planned") for w in whats) and any(w.startswith("Delivered ·") for w in whats)
    assert "Fully delivered" in whats
    approved = next(x for x in h if x["what"] == "Approved")
    assert approved["who"] and approved["kind"] == "order"
    assert [x["at"] for x in h] == sorted(x["at"] for x in h)


class FakeTally:
    def __init__(self, url, company="", timeout=60):
        pass

    def _collection(self, ctype, fetch, filt=None, from_date=None, to_date=None, formulae=None, **k):
        FakeTally.calls.append((ctype, filt, formulae))
        if ctype == "Ledger":
            return ET.fromstring('<E><LEDGER NAME="Sharma Building Material"><CLOSINGBALANCE>-50000.00</CLOSINGBALANCE>'
                                 "</LEDGER></E>")
        v = ("<VOUCHER><DATE>20261102</DATE><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>145</VOUCHERNUMBER>"
             "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Sharma Building Material</LEDGERNAME><AMOUNT>-140000.00</AMOUNT>"
             "</ALLLEDGERENTRIES.LIST><ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME><AMOUNT>140000.00</AMOUNT>"
             "</ALLLEDGERENTRIES.LIST></VOUCHER>"
             "<VOUCHER><DATE>20261105</DATE><VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME><VOUCHERNUMBER>88</VOUCHERNUMBER>"
             "<NARRATION>NEFT</NARRATION><ALLLEDGERENTRIES.LIST><LEDGERNAME>Sharma Building Material</LEDGERNAME>"
             "<AMOUNT>100000.00</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER>")
        return ET.fromstring(f"<E>{v}</E>")


def test_dealer_statement_from_tally(world, monkeypatch):
    shop = Shop(world)
    FakeTally.calls = []
    monkeypatch.setattr(tally_io, "Tally", FakeTally)
    monkeypatch.setattr(psvc, "UPLOADER_DIR", __import__("pathlib").Path("/nonexistent"))
    world.db.add(SystemSetting(company_id=shop.co.id, key="tally.url", value={"v": "http://tally.test:9000"}))
    world.db.commit()
    r = get(shop, shop.sales, f"/dealers/{shop.dealer.id}/statement", date_from="2026-11-01", date_to="2026-11-15")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["opening"] == "50000.00" and s["closing"] == "90000.00"
    assert [(x["type"], x["debit"], x["credit"], x["balance"]) for x in s["rows"]] == [
        ("Sales", "140000.00", "0.00", "190000.00"), ("Receipt", "0.00", "100000.00", "90000.00")]
    assert s["rows"][1]["narration"] == "NEFT"
    ctype, filt, formulae = FakeTally.calls[1]
    assert ctype == "Voucher" and "Sharma Building Material" in filt and "NOT $IsOptional" in filt
    assert formulae == {"PUIsDealer": '$LedgerName = "Sharma Building Material"'}
    assert get(shop, shop.sales, f"/dealers/{shop.other.id}/statement").status_code == 404  # not their dealer
    assert get(shop, shop.sales, f"/dealers/{shop.dealer.id}/statement", date_from="2025-01-01",
               date_to=str(date(2026, 11, 15))).status_code == 422  # more than a year


def test_daily_dealerwise_report(world):
    shop = Shop(world)
    booked_and_delivered(shop, "300")
    booked_and_delivered(shop, "200")  # same dealer, product and rate: one line of 500
    o = shop.order(shop.ops, bags="100", rate="360", dealer=shop.other)  # Gupta at a hand-entered rate
    assert o.status_code == 201, o.text
    shop.post(shop.owner, f"/orders/{o.json()['id']}/approve")
    deliver(shop, o.json())
    r = get(shop, shop.owner, "/reports/daily").json()
    assert [d["dealer"] for d in r["dealers"]] == ["Gupta Traders", "Sharma Building Material"]
    gupta, sharma = r["dealers"]
    assert sharma["lines"] == [{"product": "Ambuja PPC", "qty": "500", "unit": "BAG", "rate": "350.00", "rate_unit": "BAG",
                                "amount": "175000.00", "orders": sharma["orders"]}]
    assert len(sharma["orders"]) == 2 and sharma["trucks"] == 2 and sharma["bags"] == "500"
    assert gupta["lines"][0]["rate"] == "360.00" and gupta["amount"] == "36000.00"
    assert r["total"] == {"amount": "211000.00", "bags": "600", "tonnes": "30.00", "trucks": 3, "dealers": 2}
    assert r["products"] == [{"product": "Ambuja PPC", "unit": "BAG", "qty": "600", "amount": "211000.00"}]
    yesterday = get(shop, shop.owner, "/reports/daily", day=str(date.fromordinal(date.today().toordinal() - 1))).json()
    assert yesterday["dealers"] == []
    x = get(shop, shop.owner, "/reports/daily", format="xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    import io

    from openpyxl import load_workbook

    ws = load_workbook(io.BytesIO(x.content))["Dealer-wise"]
    cells = [[c.value for c in row] for row in ws.iter_rows(min_row=4)]
    assert cells[0][:7] == ["Gupta Traders", "Ambuja PPC", 100, "BAG", 360, "BAG", 36000]
    assert any(row[0] == "GRAND TOTAL" and row[6] == 211000 for row in cells)
