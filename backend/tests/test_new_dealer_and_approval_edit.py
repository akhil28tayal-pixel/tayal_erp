"""A dealer added while booking (made in Tally too), and a super admin changing quantity / rate while approving."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.modules.masters.models import Dealer, DealerContact
from app.modules.masters.schemas import TallyLedgerIn
from app.modules.masters.service import upsert_dealers_from_tally
from app.modules.purchases import tally_io
from tests.test_orders_dispatch import API, Shop


@pytest.fixture
def shop(world):
    return Shop(world)


NEW = {"name": "Verma  Cement Store", "mobile": "+91 98100 55555", "contact_name": "Rakesh Verma",
       "address": "Shop 4, Main Road", "city": "Najafgarh", "pincode": "110043"}


def test_sales_adds_a_dealer_and_books_for_it_when_tally_is_off(shop, monkeypatch):
    def down(self, *a, **k):
        raise tally_io.TallyError("Cannot reach Tally")
    monkeypatch.setattr(tally_io.Tally, "create_ledger", down)
    r = shop.post(shop.sales, "/dealers", NEW)
    assert r.status_code == 201, r.text
    out = r.json()
    d = out["dealer"]
    assert d["name"] == "Verma Cement Store" and d["source"] == "ERP" and d["pending_in_tally"] is True
    assert out["in_tally"] is False and "next Tally sync" in out["tally"]
    contact = shop.db.execute(select(DealerContact).where(DealerContact.dealer_id == d["id"])).scalar_one()
    assert contact.phone == "9810055555" and contact.is_primary

    # the sales executive can book for it straight away (it is assigned to them); no rate on file → needs a rate
    o = shop.w.client.post(f"{API}/orders", headers=shop.h(shop.sales), json={"dealer_id": d["id"], "lines": [
        {"product_id": str(shop.ppc.id), "qty": "50", "uom": "BAG", "godown_id": str(shop.g1.id), "rate": "355"}]})
    assert o.status_code == 201, o.text
    assert o.json()["status"] == "UNDER_REVIEW"

    # same name again → points to the existing one
    again = shop.post(shop.sales, "/dealers", {"name": "verma cement store"})
    assert again.status_code == 409 and again.json()["dealer_id"] == d["id"]
    assert shop.post(shop.sales, "/dealers", {"name": "Sharma Building Material"}).status_code == 409


def test_made_in_tally_and_linked_at_the_next_sync(shop, monkeypatch):
    sent = {}

    def ok(self, name, **k):
        sent.update(name=name, **k)
        return {"ok": True}
    monkeypatch.setattr(tally_io.Tally, "create_ledger", ok)
    from app.modules.integrations.tally import sales

    monkeypatch.setattr(sales, "_client", lambda db, cid: tally_io.Tally("http://tally.test:9000", "ITD"))
    r = shop.post(shop.ops, "/dealers", {**NEW, "gstin": "07abcde1234f1z5", "group": "Sundry Debtors (Delhi)"})
    assert r.status_code == 201, r.text
    assert r.json()["in_tally"] is True
    assert sent["name"] == "Verma Cement Store" and sent["parent"] == "Sundry Debtors (Delhi)"
    assert sent["mobile"] == "9810055555" and sent["gstin"] == "07ABCDE1234F1Z5" and sent["state"] == "Delhi"

    # the next sync sees the ledger: same dealer, now linked (no duplicate)
    upsert_dealers_from_tally(shop.db, shop.co.id, [
        TallyLedgerIn(guid="L9", name="Verma Cement Store", parent="Sundry Debtors (Delhi)", balance=Decimal("0"))],
        None)
    shop.db.commit()
    n = shop.db.execute(select(func.count()).select_from(Dealer).where(Dealer.name == "Verma Cement Store")).scalar()
    d = shop.db.execute(select(Dealer).where(Dealer.tally_ledger_guid == "L9")).scalar_one()
    assert n == 1 and str(d.id) == r.json()["dealer"]["id"] and d.pending_in_tally is False


def test_only_staff_with_the_permission_add_dealers(shop):
    driver_like = shop.w.user(shop.co, ["COLLECTION_EXECUTIVE"])
    assert shop.post(driver_like, "/dealers", NEW).status_code == 403


def _waiting(shop, bags="100"):
    o = shop.order(shop.sales, bags=bags, rate="340").json()  # below the 350 rate → waits for approval
    assert o["status"] in ("UNDER_REVIEW", "ON_HOLD"), o["status"]
    return o


def test_super_admin_changes_qty_and_rate_while_approving(shop):
    o = _waiting(shop)
    boss = shop.w.user(shop.co, ["SUPER_ADMIN"])
    shop.db.commit()
    change = {"lines": [{"line_no": 1, "qty": "120", "rate": "348"}]}
    # an operations manager or the owner can approve, but not change it
    assert shop.post(shop.ops, f"/orders/{o['id']}/approve", change).status_code == 403
    assert shop.post(shop.owner, f"/orders/{o['id']}/approve", change).status_code == 403
    r = shop.post(boss, f"/orders/{o['id']}/approve", change)
    assert r.status_code == 200, r.text
    x = r.json()
    assert x["status"] == "APPROVED"
    it = x["items"][0]
    assert it["qty"] == "120.000" and Decimal(it["rate"]) == Decimal("348")
    assert x["total_amount"] == "41760.00"  # 120 × 348
    hist = shop.w.client.get(f"{API}/orders/{o['id']}/history", headers=shop.h(boss)).json()
    h = [h for h in hist if h["what"] == "Changed while approving"]
    assert h and "qty 100 → 120 bag" in h[0]["detail"] and "rate ₹340 → ₹348 /bag" in h[0]["detail"]


def test_approve_without_changes_still_works_and_bad_line_is_refused(shop):
    o = _waiting(shop)
    boss = shop.w.user(shop.co, ["SUPER_ADMIN"])
    shop.db.commit()
    r = shop.post(boss, f"/orders/{o['id']}/approve", {"lines": [{"line_no": 7, "qty": "5"}]})
    assert r.status_code == 422
    r = shop.post(boss, f"/orders/{o['id']}/approve", {})
    assert r.status_code == 200 and r.json()["total_amount"] == "34000.00"


def test_ledger_xml_sent_to_tally():
    import xml.etree.ElementTree as ET

    seen = {}

    def post(self, xml, timeout=None):
        seen["xml"] = xml
        return ET.fromstring("<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ERRORS>0</ERRORS>"
                             "</IMPORTRESULT></DATA></BODY></ENVELOPE>")
    t = tally_io.Tally("http://x:9000", "Ishika Traders Daily")
    t.post = post.__get__(t)
    assert t.create_ledger("A & B Traders", mobile="9810055555", address=["Shop 4", "Najafgarh"], state="Delhi",
                           gstin="07ABCDE1234F1Z5") == {"ok": True}
    root = ET.fromstring(seen["xml"])  # well-formed, names escaped
    led = root.find(".//LEDGER")
    assert led.get("NAME") == "A & B Traders" and led.findtext("PARENT") == "Sundry Debtors"
    assert led.findtext("LEDGERMOBILE") == "9810055555" and led.findtext("PARTYGSTIN") == "07ABCDE1234F1Z5"
    assert root.findtext(".//SVCURRENTCOMPANY") == "Ishika Traders Daily"

    def exists(self, xml, timeout=None):
        return ET.fromstring("<ENVELOPE><CREATED>0</CREATED><ERRORS>1</ERRORS>"
                             "<LINEERROR>Ledger 'A &amp; B Traders' already exists</LINEERROR></ENVELOPE>")
    t.post = exists.__get__(t)
    assert t.create_ledger("A & B Traders")["ok"] is True
