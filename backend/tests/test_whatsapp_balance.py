from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import unquote

import pytest
from sqlalchemy import select

from app.modules.collections.models import DealerMessage
from app.modules.collections.service import inr, normalize_mobile
from app.modules.integrations.tally.xml_client import parse_ledgers
from app.modules.masters.models import Dealer, DealerAssignment, DealerContact
from app.modules.masters.schemas import TallyLedgerIn
from app.modules.masters.service import upsert_dealers_from_tally

API = "/api/v1"


@pytest.mark.parametrize("raw,expected", [
    ("9810012345", "919810012345"),
    ("+91 98100 12345", "919810012345"),
    ("09810012345", "919810012345"),
    ("011-25551234 / 9871234567", "919871234567"),  # landline first, mobile second
    ("9810012345, 9871234567", "919810012345"),
    ("011-25551234", None),
    ("12345", None),
    ("", None),
    (None, None),
])
def test_normalize_mobile(raw, expected):
    assert normalize_mobile(raw) == expected


def test_indian_grouping():
    assert inr(Decimal("185000")) == "1,85,000"
    assert inr(Decimal("12345678.5")) == "1,23,45,678.50"
    assert inr(Decimal("999")) == "999"


def test_tally_ledger_contact_fields_are_read():
    xml = """<ENVELOPE><BODY><DATA><COLLECTION><LEDGER NAME="A"><GUID>g1</GUID><PARENT>Sundry Debtors</PARENT>
    <LEDGERMOBILE>98100 12345</LEDGERMOBILE><LEDGERPHONE>011-2555</LEDGERPHONE><LEDGERCONTACT>Ramesh</LEDGERCONTACT>
    <CLOSINGBALANCE>-1500.00</CLOSINGBALANCE></LEDGER></COLLECTION></DATA></BODY></ENVELOPE>"""
    lg = parse_ledgers(xml)[0]
    assert (lg.mobile, lg.phone, lg.contact_name, lg.balance) == ("98100 12345", "011-2555", "Ramesh", Decimal("1500.00"))


@pytest.fixture
def setup(world):
    co = world.company()
    upsert_dealers_from_tally(world.db, co.id, [
        TallyLedgerIn(guid="1", name="Sharma Building Material", parent="Sundry Debtors", balance=Decimal("185000"),
                      mobile="98100 12345", contact_name="Sharma ji"),
        TallyLedgerIn(guid="2", name="Gupta Traders", parent="Sundry Debtors", balance=Decimal("-5000")),
        TallyLedgerIn(guid="3", name="No Phone Stores", parent="Sundry Debtors", balance=Decimal("20000"), phone="011-2555"),
    ], None)
    world.db.commit()
    d = {x.tally_ledger_guid: x for x in world.db.execute(select(Dealer).where(Dealer.company_id == co.id)).scalars()}
    owner = world.user(co, ["BUSINESS_OWNER"])
    return world, co, d, world.headers(owner, co)


def test_balance_list_and_summary(setup):
    world, co, d, h = setup
    r = world.client.get(f"{API}/collections/balances", headers=h).json()
    assert [x["name"] for x in r["items"]] == ["Sharma Building Material", "No Phone Stores"]  # dues, biggest first
    assert r["items"][0]["numbers"][0] == {"number": "919810012345", "label": "Tally mobile · Sharma ji"}
    assert r["items"][1]["numbers"] == []
    s = world.client.get(f"{API}/collections/summary", headers=h).json()
    assert s["dealers_with_dues"] == 2 and s["total_due"] == "205000.00" and s["advances"] == "5000.00"


def test_send_balance_builds_whatsapp_link_and_logs(setup):
    world, co, d, h = setup
    r = world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["number"] == "919810012345"
    assert out["url"].startswith("https://wa.me/919810012345?text=")
    assert "₹1,85,000" in out["message"] and "Sharma Building Material" in out["message"]
    assert unquote(out["url"].split("text=")[1]) == out["message"]
    # A second reminder within 24 h asks first.
    again = world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={}, headers=h)
    assert again.status_code == 409 and again.json()["code"] == "RECENTLY_REMINDED"
    ok = world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={"confirm_repeat": True}, headers=h)
    assert ok.status_code == 200
    row = world.client.get(f"{API}/collections/balances", headers=h).json()["items"][0]
    assert row["last_reminder_at"] and row["last_reminder_balance"] == "185000.00"


def test_erp_contact_beats_tally_and_custom_number(setup):
    world, co, d, h = setup
    world.db.add(DealerContact(dealer_id=d["1"].id, name="Accounts", phone="9871234567", is_primary=True))
    world.db.commit()
    out = world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={}, headers=h).json()
    assert out["number"] == "919871234567"
    bad = world.client.post(f"{API}/dealers/{d['3'].id}/whatsapp/balance", json={"number": "12"}, headers=h)
    assert bad.json()["code"] == "INVALID_MOBILE"


def test_refusals(setup):
    world, co, d, h = setup
    assert world.client.post(f"{API}/dealers/{d['2'].id}/whatsapp/balance", json={}, headers=h).json()["code"] == "NOTHING_DUE"
    assert world.client.post(f"{API}/dealers/{d['3'].id}/whatsapp/balance", json={}, headers=h).json()["code"] == "NO_MOBILE"


def test_collection_executive_sees_only_assigned_and_sales_cannot_send(setup):
    world, co, d, h = setup
    emp = world.employee(co, "C1", "Collector")
    coll = world.user(co, ["COLLECTION_EXECUTIVE"], employee=emp)
    world.db.add(DealerAssignment(dealer_id=d["3"].id, employee_id=emp.id, kind="COLLECTION",
                                  valid_from=datetime.now(UTC).date()))
    world.db.commit()
    hc = world.headers(coll, co)
    assert [x["name"] for x in world.client.get(f"{API}/collections/balances", headers=hc).json()["items"]] == ["No Phone Stores"]
    assert world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={}, headers=hc).status_code == 404
    sales = world.user(co, ["SALES_EXECUTIVE"])
    assert world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={},
                             headers=world.headers(sales, co)).status_code == 403


def test_add_edit_delete_mobile_numbers(setup):
    world, co, d, h = setup
    dealer = d["3"]  # no mobile in Tally
    base = f"{API}/dealers/{dealer.id}/contacts"
    bad = world.client.post(base, json={"name": "Owner", "phone": "011-2555"}, headers=h)
    assert bad.status_code == 422 and bad.json()["detail"] == "Enter a 10-digit mobile number"
    c1 = world.client.post(base, json={"name": "Owner", "phone": "+91 98100-55555"}, headers=h).json()
    assert c1["phone"] == "9810055555" and c1["is_primary"] is True  # the first number becomes primary
    assert world.client.post(base, json={"name": "Again", "phone": "9810055555"}, headers=h).status_code == 409
    world.client.post(base, json={"name": "Munshi", "phone": "9871234567", "is_primary": True}, headers=h).json()
    detail = world.client.get(f"{API}/dealers/{dealer.id}", headers=h).json()
    assert {c["name"]: c["is_primary"] for c in detail["contacts"]} == {"Owner": False, "Munshi": True}
    # WhatsApp now goes to the primary number.
    out = world.client.post(f"{API}/dealers/{dealer.id}/whatsapp/balance", json={}, headers=h).json()
    assert out["number"] == "919871234567"
    upd = world.client.put(f"{base}/{c1['id']}", json={"name": "Owner", "phone": "9999988888", "is_primary": True},
                           headers=h).json()
    assert upd["phone"] == "9999988888"
    assert world.client.delete(f"{base}/{c1['id']}", headers=h).status_code == 204
    detail = world.client.get(f"{API}/dealers/{dealer.id}", headers=h).json()
    assert [(c["name"], c["is_primary"]) for c in detail["contacts"]] == [("Munshi", True)]  # primary handed on


def test_collection_staff_can_add_numbers_sales_cannot(setup):
    world, co, d, h = setup
    emp = world.employee(co, "C2", "Collector")
    coll = world.user(co, ["COLLECTION_EXECUTIVE"], employee=emp)
    world.db.add(DealerAssignment(dealer_id=d["3"].id, employee_id=emp.id, kind="COLLECTION",
                                  valid_from=datetime.now(UTC).date()))
    world.db.commit()
    r = world.client.post(f"{API}/dealers/{d['3'].id}/contacts", json={"name": "Owner", "phone": "9810055555"},
                          headers=world.headers(coll, co))
    assert r.status_code == 201
    sales = world.user(co, ["SALES_EXECUTIVE"])
    r = world.client.post(f"{API}/dealers/{d['3'].id}/contacts", json={"name": "X", "phone": "9810055556"},
                          headers=world.headers(sales, co))
    assert r.status_code == 403


# ------------------------------------------------------------------ Meta Cloud API (HTTP calls are faked)
class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


@pytest.fixture
def cloud(setup, monkeypatch):
    import httpx

    from app.core.config import get_settings
    from app.modules.platform.models import SystemSetting

    world, co, d, h = setup
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_token", "EAAG-test")
    monkeypatch.setattr(s, "whatsapp_phone_number_id", "1234567890")
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers))
        if json["to"] == "919999999999":
            return _Resp(400, {"error": {"code": 131026, "message": "Message undeliverable",
                                         "error_data": {"details": "Recipient not on WhatsApp"}}})
        return _Resp(200, {"messaging_product": "whatsapp", "messages": [{"id": "wamid.ABC"}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    world.client.put(f"{API}/settings/whatsapp.provider", json={"value": "CLOUD_API"}, headers=h)
    world.db.add(SystemSetting(company_id=co.id, key="whatsapp.sender_name", value={"v": "Ishika Traders"}))
    world.db.commit()
    return world, co, d, h, calls


def test_cloud_api_sends_template_directly(cloud):
    world, co, d, h, calls = cloud
    r = world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["url"] is None and r.json()["provider"] == "CLOUD_API"
    url, body, headers = calls[0]
    assert url == "https://graph.facebook.com/v22.0/1234567890/messages"
    assert headers["Authorization"] == "Bearer EAAG-test"
    assert body["to"] == "919810012345" and body["template"]["name"] == "tayal_balance_due"
    params = [p["text"] for p in body["template"]["components"][0]["parameters"]]
    assert len(params) == 3 and params[:2] == ["Sharma Building Material", "1,85,000"]  # dealer, balance, as_of
    msgs = world.db.execute(select(DealerMessage)).scalars().all()
    assert [(m.status, m.provider_message_id) for m in msgs] == [("SENT", "wamid.ABC")]


def test_cloud_api_failure_is_logged_and_explained(cloud):
    world, co, d, h, calls = cloud
    r = world.client.post(f"{API}/dealers/{d['3'].id}/whatsapp/balance", json={"number": "9999999999"}, headers=h)
    assert r.status_code == 502 and "not on WhatsApp" in r.json()["detail"]
    world.db.expire_all()
    m = world.db.execute(select(DealerMessage)).scalar_one()
    assert m.status == "FAILED" and "131026" in m.error


def test_status_and_test_message(cloud):
    world, co, d, h, calls = cloud
    st = world.client.get(f"{API}/collections/whatsapp", headers=h).json()
    assert st["api_keys_set"] and st["phone_number_id"] == "…7890" and st["provider"] == "CLOUD_API"
    assert "EAAG" not in str(st)  # the token is never shown
    t = world.client.post(f"{API}/collections/whatsapp/test", json={"number": "98100 12345"}, headers=h)
    assert t.status_code == 200 and t.json()["provider_message_id"] == "wamid.ABC"


def test_cannot_switch_to_cloud_without_keys(setup):
    world, co, d, h = setup
    r = world.client.put(f"{API}/settings/whatsapp.provider", json={"value": "CLOUD_API"}, headers=h)
    assert r.status_code == 422 and ".env" in r.json()["detail"]


def test_template_parameter_order_is_configurable(cloud):
    world, co, d, h, calls = cloud
    r = world.client.put(f"{API}/settings/whatsapp.template_params", json={"value": "sender,dealer,balance,as_of"}, headers=h)
    assert r.status_code == 200, r.text
    world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={}, headers=h)
    params = [p["text"] for p in calls[-1][1]["template"]["components"][0]["parameters"]]
    assert params[:3] == ["Ishika Traders", "Sharma Building Material", "1,85,000"]
    bad = world.client.put(f"{API}/settings/whatsapp.template_params", json={"value": "dealer,amount"}, headers=h)
    assert bad.status_code == 422


def test_default_message_text(setup):
    world, co, d, h = setup
    out = world.client.post(f"{API}/dealers/{d['1'].id}/whatsapp/balance", json={}, headers=h).json()
    assert out["message"].startswith("*Payment Reminder*\nDear Sharma Building Material, your outstanding balance with ")
    assert "is ₹1,85,000 as on " in out["message"] and out["message"].endswith("kindly ignore this message.")


# ------------------------------------------------------------------ send to everyone
def test_bulk_needs_automatic_sending(setup):
    world, co, d, h = setup
    r = world.client.post(f"{API}/collections/whatsapp/bulk", json={}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "AUTOMATIC_SENDING_OFF"


def test_bulk_preview_send_and_skip_rules(cloud):
    world, co, d, h, calls = cloud
    # Dealer 3 gets a number that WhatsApp rejects, so one send fails.
    world.db.add(DealerContact(dealer_id=d["3"].id, name="Owner", phone="9999999999", is_primary=True))
    world.db.add(Dealer(company_id=co.id, code="D9", name="Tiny Dues", tally_balance=Decimal("500"),
                        tally_mobile="9810000009", tally_balance_at=datetime.now(UTC)))
    world.db.commit()
    p = world.client.post(f"{API}/collections/whatsapp/bulk/preview", json={}, headers=h).json()
    assert (p["to_send"], p["no_mobile"], p["recently_reminded"]) == (3, 0, 0)
    assert p["sample"][0]["name"] == "Sharma Building Material"  # biggest dues first
    p = world.client.post(f"{API}/collections/whatsapp/bulk/preview", json={"min_balance": "1000"}, headers=h).json()
    assert p["to_send"] == 2 and p["to_send_amount"] == "205000.00"

    r = world.client.post(f"{API}/collections/whatsapp/bulk", json={"min_balance": "1000"}, headers=h)
    assert r.status_code == 202, r.text
    run = world.client.get(f"{API}/collections/whatsapp/bulk", headers=h).json()  # background task already ran
    assert (run["status"], run["total"], run["sent"], run["failed"]) == ("DONE", 2, 1, 1)
    assert run["failures"][0]["dealer"] == "No Phone Stores" and "not on WhatsApp" in run["failures"][0]["error"]
    assert run["started_by_name"]

    # Just reminded → skipped next time unless asked.
    p = world.client.post(f"{API}/collections/whatsapp/bulk/preview", json={"min_balance": "1000"}, headers=h).json()
    assert p["recently_reminded"] == 1 and p["to_send"] == 1  # the failed one was not "reminded"
    p = world.client.post(f"{API}/collections/whatsapp/bulk/preview",
                          json={"min_balance": "1000", "skip_recent": False}, headers=h).json()
    assert p["to_send"] == 2


def test_bulk_skips_blocked_and_respects_visibility(cloud):
    world, co, d, h, calls = cloud
    d["1"].status, d["1"].blocked_reason = "BLOCKED", "Doubtful debt"
    world.db.commit()
    p = world.client.post(f"{API}/collections/whatsapp/bulk/preview", json={}, headers=h).json()
    assert p["blocked"] == 1 and p["to_send"] == 0 and p["no_mobile"] == 1
    p = world.client.post(f"{API}/collections/whatsapp/bulk/preview", json={"include_blocked": True}, headers=h).json()
    assert p["to_send"] == 1
    emp = world.employee(co, "C3", "Collector")
    coll = world.user(co, ["COLLECTION_EXECUTIVE"], employee=emp)
    hc = world.headers(coll, co)
    p = world.client.post(f"{API}/collections/whatsapp/bulk/preview", json={"include_blocked": True}, headers=hc).json()
    assert p["to_send"] == 0  # nothing assigned to this collector
