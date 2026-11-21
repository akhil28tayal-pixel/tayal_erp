"""Delivered orders go to Tally as Optional Sales vouchers; the admin approves (makes Regular) or deletes them in
Tally and the ERP reads that back."""

import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import select

from app.modules.integrations.tally.models import TallySale
from app.modules.purchases import service as psvc
from app.modules.purchases import tally_io
from tests.test_orders_dispatch import API, Shop


class FakeTally:
    imported: list = []
    in_tally: list = []  # what a Voucher export returns: dicts with ref, optional, number
    ledgers = [{"name": "Sales", "parent": "Sales Accounts"}, {"name": "Cash", "parent": "Cash-in-Hand"},
               {"name": "Sharma Building Material", "parent": "Sundry Debtors"}]
    fail: str | None = None

    def __init__(self, url, company="", timeout=60):
        self.company = company

    def masters(self):
        if FakeTally.fail:
            raise tally_io.TallyError(FakeTally.fail)
        return {"ledgers": FakeTally.ledgers, "items": [{"name": "Ambuja", "unit": "Bag"}], "godowns": []}

    def import_voucher(self, xml):
        FakeTally.imported.append(xml)
        return {"ok": True, "vchid": str(900 + len(FakeTally.imported))}

    def _collection(self, ctype, fetch, filt=None, from_date=None, to_date=None, **k):
        rows = "".join(f"<VOUCHER><REFERENCE>{v['ref']}</REFERENCE><VOUCHERNUMBER>{v.get('number', '')}</VOUCHERNUMBER>"
                       f"<ISOPTIONAL>{'Yes' if v['optional'] else 'No'}</ISOPTIONAL></VOUCHER>" for v in FakeTally.in_tally)
        return ET.fromstring(f"<E>{rows}</E>")


@pytest.fixture
def shop(world, monkeypatch):
    monkeypatch.setattr(tally_io, "Tally", FakeTally)
    FakeTally.imported, FakeTally.in_tally, FakeTally.fail = [], [], None
    FakeTally.ledgers = [{"name": "Sales", "parent": "Sales Accounts"}, {"name": "Cash", "parent": "Cash-in-Hand"},
                         {"name": "Sharma Building Material", "parent": "Sundry Debtors"}]
    monkeypatch.setattr(psvc, "UPLOADER_DIR", __import__("pathlib").Path("/nonexistent"))
    s = Shop(world)
    from app.modules.platform.models import SystemSetting

    world.db.add(SystemSetting(company_id=s.co.id, key="tally.url", value={"v": "http://tally.test:9000"}))
    world.db.commit()
    s.admin = world.user(s.co, ["SUPER_ADMIN"])
    return s


def deliver_order(shop, bags="400", split=("300", "100"), short=None):
    o = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags=bags).json()['id']}/approve", {}).json()
    line = o["items"][0]["id"]
    for n, (q, g) in enumerate(zip(split, (shop.g1, shop.g2)[:len(split)], strict=True)):
        d = shop.post(shop.ops, "/delivery-orders", {
            "sales_order_id": o["id"], "godown_id": str(g.id), "vehicle_id": str(shop.truck.id),
            "driver_id": str(shop.driver.id), "send_now": True, "lines": [{"order_item_id": line, "qty": q}]}).json()
        assert "id" in d, (o, d)
        body = {"recipient_name": "Site"}
        if short and n == len(split) - 1:
            body["lines"] = [{"do_item_id": d["items"][0]["id"], "delivered_qty": str(int(q) - short),
                              "short_qty": str(short)}]
        r = shop.post(shop.ops, f"/delivery-orders/{d['id']}/deliver", body)
        assert r.status_code == 200, r.text
    return shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()


def test_delivered_order_goes_to_tally_as_optional_and_admin_approves(shop):
    o = deliver_order(shop)
    assert o["status"] == "DELIVERED"
    # pushed straight after the delivery was recorded
    assert o["tally_status"] == "WAITING_APPROVAL" and len(FakeTally.imported) == 1
    x = FakeTally.imported[0]
    assert "<ISOPTIONAL>Yes</ISOPTIONAL>" in x and f"<REFERENCE>{o['order_no']}</REFERENCE>" in x
    assert "<PARTYLEDGERNAME>Sharma Building Material</PARTYLEDGERNAME>" in x
    assert "<STOCKITEMNAME>Ambuja</STOCKITEMNAME>" in x and "<ACTUALQTY> 400 Bag</ACTUALQTY>" in x
    assert "<GODOWNNAME>Nasirpur</GODOWNNAME>" in x and "<GODOWNNAME>Uttam Nagar</GODOWNNAME>" in x
    assert "<ACTUALQTY> 300 Bag</ACTUALQTY>" in x and "<AMOUNT>140000.00</AMOUNT>" in x
    assert "<LEDGERNAME>Sales</LEDGERNAME>" in x  # the only ledger under Sales Accounts

    # Still optional in Tally → still waiting
    FakeTally.in_tally = [{"ref": o["order_no"], "optional": True}]
    assert shop.post(shop.admin, "/tally/sales/check").json()["waiting"] == 1
    # Admin marks it Regular in Tally → approved
    FakeTally.in_tally = [{"ref": o["order_no"], "optional": False, "number": "1457"}]
    assert shop.post(shop.admin, "/tally/sales/check").json()["approved"] == 1
    o = shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()
    assert o["tally_status"] == "APPROVED" and o["tally_note"] == "Tally voucher 1457"
    lst = shop.w.client.get(f"{API}/tally/sales", headers=shop.h(shop.admin)).json()
    assert lst["counts"] == {"APPROVED": 1} and lst["items"][0]["amount"] == "140000.00"


def test_deleted_in_tally_is_rejected_and_can_be_sent_again(shop):
    o = deliver_order(shop)
    other = {"ref": "SOMETHING-ELSE", "optional": True}
    FakeTally.in_tally = [other]
    assert shop.post(shop.admin, "/tally/sales/check").json()["rejected"] == 1
    sale = shop.db.execute(select(TallySale)).scalar_one()
    shop.db.refresh(sale)
    assert sale.status == "REJECTED" and "deleted" in sale.error
    r = shop.post(shop.admin, "/tally/sales/push", {"ids": [str(sale.id)]})
    assert r.json()["pushed"] == 1 and len(FakeTally.imported) == 2
    assert shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()["tally_status"] == "WAITING_APPROVAL"


def test_failure_is_kept_and_retried(shop):
    FakeTally.ledgers = [{"name": "Sales A", "parent": "Sales Accounts"}, {"name": "Sales B", "parent": "Sales Accounts"},
                         {"name": "Sharma Building Material", "parent": "Sundry Debtors"}]
    o = deliver_order(shop)
    assert o["tally_status"] == "FAILED" and "sales ledger" in o["tally_note"]
    # choose the ledger, then retry
    r = shop.w.client.put(f"{API}/settings/tally.sales_ledger", json={"value": "Sales B"}, headers=shop.h(shop.admin))
    assert r.status_code == 200, r.text
    sale = shop.db.execute(select(TallySale)).scalar_one()
    assert shop.post(shop.admin, "/tally/sales/push", {"ids": [str(sale.id)]}).json()["pushed"] == 1
    assert "<LEDGERNAME>Sales B</LEDGERNAME>" in FakeTally.imported[-1]
    # Tally switched off: the order still delivers, the sale just waits as failed
    FakeTally.fail = "Cannot reach Tally"
    o2 = deliver_order(shop, bags="100", split=("100",))
    assert o2["status"] == "DELIVERED" and o2["tally_status"] == "FAILED"


def test_shortage_and_closed_orders_send_only_what_was_delivered(shop):
    o = deliver_order(shop, short=40)  # 300 + (100 sent, 40 short)
    assert o["status"] == "DELIVERED" and o["tally_status"] == "WAITING_APPROVAL"
    x = FakeTally.imported[-1]
    assert "<ACTUALQTY> 360 Bag</ACTUALQTY>" in x and "<AMOUNT>126000.00</AMOUNT>" in x

    # Part of an order delivered, the rest never sent: closing it sends what was delivered
    o = deliver_order(shop, bags="350", split=("250",))  # (a second 400-bag order the same day = duplicate)
    assert o["status"] == "PARTIALLY_DELIVERED" and o["tally_status"] is None  # not finished yet
    r = shop.post(shop.ops, f"/orders/{o['id']}/close", {"reason": "dealer took only 250"})
    assert r.status_code == 200 and r.json()["status"] == "CLOSED", r.text
    o = shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()
    assert o["tally_status"] == "WAITING_APPROVAL" and "<ACTUALQTY> 250 Bag</ACTUALQTY>" in FakeTally.imported[-1]
    # Sales people cannot push
    assert shop.post(shop.sales, "/tally/sales/push", {"ids": None}).status_code == 403


def test_voucher_made_with_exceptions_is_not_sent_again():
    """Tally made it but listed it under Exceptions: it is in Tally, so resending would make a duplicate."""
    t = tally_io.Tally.__new__(tally_io.Tally)
    t.company = "ITD"
    t.post = lambda xml, timeout=None: ET.fromstring(
        "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ERRORS>0</ERRORS><EXCEPTIONS>1</EXCEPTIONS>"
        "<LASTVCHID>77</LASTVCHID></IMPORTRESULT></DATA></BODY></ENVELOPE>")
    r = t.import_voucher("<VOUCHER/>")
    assert r["ok"] is True and r["vchid"] == "77" and "Exceptions" in r["warning"]
    t.post = lambda xml, timeout=None: ET.fromstring(
        "<ENVELOPE><CREATED>0</CREATED><ERRORS>1</ERRORS><LINEERROR>Stock Item 'X' does not exist</LINEERROR></ENVELOPE>")
    assert t.import_voucher("<VOUCHER/>") == {"ok": False, "error": "Stock Item 'X' does not exist"}


def test_exception_voucher_waits_and_the_sync_does_not_resend_it(shop, monkeypatch):
    def made_with_warning(self, xml):
        FakeTally.imported.append(xml)
        return {"ok": True, "vchid": "901", "warning": "Tally made the voucher but put it under Exceptions"}
    monkeypatch.setattr(FakeTally, "import_voucher", made_with_warning)
    o = deliver_order(shop, bags="100", split=("100",))
    assert o["tally_status"] == "WAITING_APPROVAL" and "Exceptions" in o["tally_note"]
    from app.modules.integrations.tally.sync import _sales_push

    assert _sales_push(shop.db, shop.co.id, None) == ("OK", "Nothing to send")
    assert len(FakeTally.imported) == 1


def test_nothing_delivered_is_never_sent_as_an_empty_voucher(shop):
    o = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags='100').json()['id']}/approve", {}).json()
    d = shop.post(shop.ops, "/delivery-orders", {
        "sales_order_id": o["id"], "godown_id": str(shop.g1.id), "vehicle_id": str(shop.truck.id),
        "driver_id": str(shop.driver.id), "send_now": True,
        "lines": [{"order_item_id": o["items"][0]["id"], "qty": "100"}]}).json()
    r = shop.post(shop.ops, f"/delivery-orders/{d['id']}/deliver", {"recipient_name": "Site", "close_rest": True,
                  "lines": [{"do_item_id": d["items"][0]["id"], "delivered_qty": "0", "returned_qty": "100"}]})
    assert r.status_code == 200, r.text
    assert FakeTally.imported == []  # nothing billed, nothing sent


def test_item_or_dealer_missing_in_tally_is_not_sent(shop, monkeypatch):
    """TallyPrime would file it under Exceptions without its items: stop with a clear message instead."""
    monkeypatch.setattr(FakeTally, "masters", lambda self: {"ledgers": FakeTally.ledgers, "godowns": [],
                                                            "items": [{"name": "Ambuja PPC 50kg", "unit": "Bag"}]})
    o = deliver_order(shop, bags="100", split=("100",))
    assert o["tally_status"] == "FAILED" and "is not a stock item in Tally" in o["tally_note"]
    assert FakeTally.imported == []
    FakeTally.ledgers = [x for x in FakeTally.ledgers if x["parent"] != "Sundry Debtors"]
    monkeypatch.setattr(FakeTally, "masters", lambda self: {"ledgers": FakeTally.ledgers, "godowns": [],
                                                            "items": [{"name": "Ambuja", "unit": "Bag"}]})
    from app.modules.integrations.tally.sync import _sales_push

    assert _sales_push(shop.db, shop.co.id, None)[0] == "FAILED"
    sale = shop.db.execute(select(TallySale)).scalar_one()
    assert "has no ledger in Tally" in sale.error and FakeTally.imported == []
