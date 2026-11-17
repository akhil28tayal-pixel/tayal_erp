"""Orders from WhatsApp messages: pasted/shared by staff or sent to the business number; read by Claude (or the
built-in reader without an API key) into a draft order for staff to check and book."""

import hashlib
import hmac
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.collections.models import DealerMessage
from app.modules.orders.models import SalesOrder
from app.modules.platform.models import Notification
from app.modules.wa_orders import reader
from app.modules.wa_orders.models import WhatsAppOrderMessage
from tests.test_orders_dispatch import API, Shop


@pytest.fixture
def shop(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    return Shop(world)


def paste(shop, user, text, **kw):
    return shop.post(user, "/whatsapp-orders/read", {"text": text, **kw})


def fake_ai(monkeypatch, answer):
    seen = {}

    def ai(text, sender, candidates, products, today):
        seen.update(text=text, sender=sender, candidates=[c.name for c in candidates], products=products)
        return answer(products, candidates) if callable(answer) else answer

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(reader, "_ai", ai)
    return seen


def test_ai_reads_hinglish_into_a_draft(shop, monkeypatch):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    seen = fake_ai(monkeypatch, lambda products, cands: {
        "is_order": True, "dealer_id": str(cands[0].id), "dealer_hint": "Sharma ji",
        "lines": [{"product_id": next(p["id"] for p in products if p["name"] == "Ambuja PPC"), "said": "200 bag ambuja",
                   "qty": 200, "unit": "bag"}],
        "delivery_date": tomorrow, "urgent": True, "site": "Rohini sec 7", "notes": None,
        "unclear": []})
    r = paste(shop, shop.sales, "Sharma ji 200 bag ambuja kal subah Rohini sec 7 pe bhejna, urgent")
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["status"] == "DRAFTED" and m["reader"] == "AI" and m["dealer_name"] == "Sharma Building Material"
    assert "Sharma Building Material" in seen["candidates"] and seen["sender"] is None
    o = shop.w.client.get(f"{API}/orders/{m['order_id']}", headers=shop.h(shop.sales)).json()
    assert o["status"] == "DRAFT" and o["source"] == "WHATSAPP" and o["priority"] == "URGENT"
    assert o["requested_delivery_date"] == tomorrow and o["site_name"] == "Rohini sec 7"
    assert o["items"][0]["product_name"] == "Ambuja PPC" and float(o["items"][0]["qty"]) == 200
    assert o["items"][0]["rate"] == "350.0000"  # the dealer's rate, not anything from the message
    assert "200 bag ambuja kal subah" in o["special_instructions"]
    # approvers hear about it
    assert shop.db.execute(select(Notification).where(Notification.user_id == shop.ops.id)).first() is None  # paste: no ping
    # staff check it and book it
    b = shop.post(shop.sales, f"/orders/{o['id']}/submit", {})
    assert b.status_code == 200 and b.json()["status"] == "UNDER_REVIEW", b.text
    lst = shop.w.client.get(f"{API}/whatsapp-orders", headers=shop.h(shop.sales)).json()
    assert lst["items"][0]["status"] == "BOOKED" and lst["items"][0]["order_no"] == b.json()["order_no"]


def test_made_up_ids_are_dropped_and_unclear_messages_wait(shop, monkeypatch):
    fake_ai(monkeypatch, {"is_order": True, "dealer_id": "00000000-0000-0000-0000-000000000000",
                          "lines": [{"product_id": "nope", "said": "2 gaadi maal", "qty": 0, "unit": "BAG"}],
                          "urgent": False, "unclear": ["'2 gaadi' is not a quantity"]})
    m = paste(shop, shop.ops, "bhai 2 gaadi maal bhej do").json()
    assert m["status"] == "NEEDS_HELP" and m["order_id"] is None
    assert "which dealer" in m["note"] and "which material" in m["note"]
    assert m["reading"]["lines"][0]["product_id"] is None and m["reading"]["unclear"]
    # finished by hand in the order form: the order carries the message's reference and is linked
    o = shop.order(shop.ops, bags="300", client_ref=m["client_ref"]).json()
    got = shop.w.client.get(f"{API}/whatsapp-orders/{m['id']}", headers=shop.h(shop.ops)).json()
    assert got["order_id"] == o["id"] and got["status"] == "BOOKED"


def test_not_an_order_and_ai_failure(shop, monkeypatch):
    fake_ai(monkeypatch, {"is_order": False, "lines": [], "urgent": False, "unclear": []})
    assert paste(shop, shop.ops, "Payment kar diya hai, check kar lo").json()["status"] == "NOT_ORDER"

    def broken(*a):
        raise reader.ReaderError("The AI service refused (401): invalid x-api-key")

    monkeypatch.setattr(reader, "_ai", broken)
    m = paste(shop, shop.ops, "200 bag ambuja").json()
    assert m["status"] == "FAILED" and "401" in m["note"]


def test_built_in_reader_without_api_key(shop):
    m = paste(shop, shop.ops, "Sharma ji 200 bag ambuja ppc kal bhejna").json()
    assert m["reader"] == "RULES" and m["status"] == "DRAFTED", m
    o = shop.w.client.get(f"{API}/orders/{m['order_id']}", headers=shop.h(shop.ops)).json()
    assert o["dealer_name"] == "Sharma Building Material" and o["items"][0]["product_name"] == "Ambuja PPC"
    assert float(o["items"][0]["qty"]) == 200 and o["items"][0]["uom"] == "BAG"
    assert o["requested_delivery_date"] == (date.today() + timedelta(days=1)).isoformat()


def test_sales_people_only_get_their_dealers(shop, monkeypatch):
    seen = fake_ai(monkeypatch, {"is_order": True, "lines": [], "urgent": False, "unclear": []})
    paste(shop, shop.sales, "Gupta traders 100 bag")
    assert "Gupta Traders" not in seen["candidates"]  # not assigned to this salesperson
    paste(shop, shop.ops, "Gupta traders 100 bag")
    assert "Gupta Traders" in seen["candidates"]


def test_ignore_throws_away_the_draft(shop):
    m = paste(shop, shop.ops, "Sharma 150 bag ambuja ppc").json()
    assert m["status"] == "DRAFTED"
    r = shop.post(shop.ops, f"/whatsapp-orders/{m['id']}/ignore").json()
    assert r["status"] == "IGNORED"
    assert shop.db.get(SalesOrder, __import__("uuid").UUID(m["order_id"])).status == "CANCELLED"


# ------------------------------------------------------------------ webhook
def signed(body: dict):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def wa_payload(msgs=None, statuses=None):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID"},
        "contacts": [{"wa_id": "919810012345", "profile": {"name": "Sharma ji"}}],
        "messages": msgs or [], "statuses": statuses or []}}]}]}


def test_webhook_receives_dealer_messages(shop, monkeypatch):
    s = get_settings()
    for k, v in {"whatsapp_verify_token": "verify-me", "whatsapp_app_secret": "app-secret",
                 "whatsapp_phone_number_id": "PNID", "whatsapp_company_code": shop.co.code}.items():
        monkeypatch.setattr(s, k, v)
    c = shop.w.client
    ok = c.get(f"{API}/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                                  "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert c.get(f"{API}/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x",
                                                    "hub.challenge": "1"}).status_code == 403
    raw, h = signed(wa_payload())
    assert c.post(f"{API}/whatsapp/webhook", content=raw, headers={**h, "X-Hub-Signature-256": "sha256=bad"}
                  ).status_code == 403

    shop.dealer.tally_mobile = "98100 12345"  # the sender is a known dealer
    shop.db.commit()
    text = {"from": "919810012345", "id": "wamid.A1", "timestamp": "1794800000", "type": "text",
            "text": {"body": "200 bag ambuja ppc bhejo"}}
    raw, h = signed(wa_payload([text]))
    assert c.post(f"{API}/whatsapp/webhook", content=raw, headers=h).status_code == 200
    c.post(f"{API}/whatsapp/webhook", content=raw, headers=h)  # Meta retries: stored once
    rows = shop.db.execute(select(WhatsAppOrderMessage)).scalars().all()
    assert len(rows) == 1
    m = rows[0]
    shop.db.refresh(m)
    assert m.status == "DRAFTED" and m.from_name == "Sharma ji" and m.source == "WHATSAPP"
    o = shop.db.get(SalesOrder, m.order_id)
    assert o.dealer_id == shop.dealer.id and o.created_by is None and o.client_ref == f"wa-{m.id}"
    n = shop.db.execute(select(Notification).where(Notification.user_id == shop.ops.id)).scalars().all()
    assert [x.title for x in n] == ["WhatsApp order: Sharma Building Material"]
    assert any(x.user_id == shop.sales.id for x in shop.db.execute(select(Notification)).scalars())  # their dealer

    # a voice note waits for a person
    voice = {"from": "919810012345", "id": "wamid.A2", "timestamp": "1794800100", "type": "audio", "audio": {}}
    raw, h = signed(wa_payload([voice]))
    c.post(f"{API}/whatsapp/webhook", content=raw, headers=h)
    v = shop.db.execute(select(WhatsAppOrderMessage).where(WhatsAppOrderMessage.wa_message_id == "wamid.A2")).scalar_one()
    assert v.status == "NEEDS_HELP" and v.dealer_id == shop.dealer.id and "Voice note" in v.note

    # delivery ticks for a message we sent
    shop.db.add(DealerMessage(company_id=shop.co.id, dealer_id=shop.dealer.id, channel="WHATSAPP", kind="BALANCE",
                              provider="CLOUD_API", phone="919810012345", body="x", status="SENT",
                              provider_message_id="wamid.OUT1"))
    shop.db.commit()
    raw, h = signed(wa_payload(statuses=[{"id": "wamid.OUT1", "status": "read"}]))
    c.post(f"{API}/whatsapp/webhook", content=raw, headers=h)
    dm = shop.db.execute(select(DealerMessage).where(DealerMessage.provider_message_id == "wamid.OUT1")).scalar_one()
    shop.db.refresh(dm)
    assert dm.status == "READ"


def test_claude_request_shape(shop, monkeypatch):
    """The real call: tool forced, only offered ids, answer taken from the tool input."""
    sent = {}

    class R:
        status_code = 200

        def json(self):
            return {"content": [{"type": "tool_use", "name": "record_order",
                                 "input": {"is_order": True, "lines": [], "urgent": False, "unclear": []}}]}

    def post(url, **kw):
        sent.update(url=url, **kw)
        return R()

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    monkeypatch.setattr(reader.httpx, "post", post)
    paste(shop, shop.ops, "Sharma 100 bag")
    assert sent["url"] == "https://api.anthropic.com/v1/messages" and sent["headers"]["x-api-key"] == "sk-test"
    j = sent["json"]
    assert j["tool_choice"] == {"type": "tool", "name": "record_order"} and j["model"] == get_settings().anthropic_model
    content = j["messages"][0]["content"]
    assert "Sharma Building Material" in content and "Ambuja PPC" in content and "Sharma 100 bag" in content


@pytest.mark.parametrize("text,want", [
    ("Sharma ji 200 bag ambuja ppc kal subah bhejna", [(200, "BAG", "Ambuja PPC")]),
    ("50 bori ambuja plus aur 100 bag shree roc", [(50, "BAG", "Ambuja Plus"), (100, "BAG", "Shree ROC")]),
    ("ambuja kawach 300", [(300, "BAG", "Ambuja Kawach")]),
    ("ambuja plus 200, shree roc 150 bags", [(200, "BAG", "Ambuja Plus"), (150, "BAG", "Shree ROC")]),
    ("shree roc 100 bag, ambuja plus 50 bag", [(100, "BAG", "Shree ROC"), (50, "BAG", "Ambuja Plus")]),
    ("Gupta ji\n200 ambuja plus\n100 shree roc", [(200, "BAG", "Ambuja Plus"), (100, "BAG", "Shree ROC")]),
    ("bhai 2 gaadi maal bhej do", []),
])
def test_built_in_reader_wording(text, want):
    products = [{"id": n, "name": n, "units": ["BAG", "MT"], "default_unit": "BAG"}
                for n in ("Ambuja Kawach", "Ambuja PPC", "Ambuja Plus", "Shree ROC")]
    got = reader._rules(text, None, [], products, date.today())["lines"]
    assert [(ln["qty"], ln["unit"], ln["product_id"]) for ln in got] == want
