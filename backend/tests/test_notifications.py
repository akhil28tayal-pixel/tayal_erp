"""Notifications (web bell / phone) and the WhatsApp sent to the dealer when a truck is delivered."""

from sqlalchemy import select

from app.modules.collections import whatsapp_cloud
from app.modules.collections.models import DealerMessage
from app.modules.platform.models import SystemSetting
from tests.test_orders_dispatch import API, Shop


def inbox(shop, user):
    return shop.w.client.get(f"{API}/notifications", headers=shop.h(user)).json()


def titles(shop, user):
    return [n["title"] for n in inbox(shop, user)["items"]]


def deliver(shop, o, recipient="Raju (site)"):
    d = shop.post(shop.ops, "/delivery-orders", {
        "sales_order_id": o["id"], "godown_id": str(shop.g1.id), "vehicle_id": str(shop.truck.id),
        "driver_id": str(shop.driver.id), "send_now": True,
        "lines": [{"order_item_id": o["items"][0]["id"], "qty": str(int(float(o["items"][0]["qty"])))}]}).json()
    r = shop.post(shop.ops, f"/delivery-orders/{d['id']}/deliver", {"recipient_name": recipient})
    assert r.status_code == 200, r.text
    return d


def test_the_right_people_hear_about_each_step(world):
    shop = Shop(world)
    o = shop.order(shop.sales, bags="300").json()
    # approvers hear about a new order; the person who booked it does not
    assert titles(shop, shop.ops) == ["New order waiting: Sharma Building Material"]
    assert inbox(shop, shop.ops)["items"][0]["body"].startswith(o["order_no"])
    assert "300 bag" in inbox(shop, shop.ops)["items"][0]["body"]
    assert inbox(shop, shop.ops)["items"][0]["link"] == f"/orders/{o['id']}"
    assert titles(shop, shop.sales) == []

    shop.post(shop.ops, f"/orders/{o['id']}/approve")
    assert titles(shop, shop.sales) == ["Approved: Sharma Building Material"]
    assert "Ready to send: Sharma Building Material" in titles(shop, shop.sales_ops)  # dispatch planners
    assert "Ready to send: Sharma Building Material" not in titles(shop, shop.ops)  # ops approved it themselves

    deliver(shop, o)
    assert titles(shop, shop.sales)[0] == "Delivered: Sharma Building Material"

    # read / unread
    me = inbox(shop, shop.sales)
    assert me["unread"] == 2
    first = me["items"][0]["id"]
    assert shop.post(shop.sales, "/notifications/read", {"ids": [first]}).json()["marked"] == 1
    assert inbox(shop, shop.sales)["unread"] == 1
    shop.post(shop.sales, "/notifications/read", {})
    assert inbox(shop, shop.sales)["unread"] == 0
    # only newer ones (what the phone asks for)
    after = inbox(shop, shop.sales)["items"][0]["created_at"]
    assert shop.w.client.get(f"{API}/notifications", params={"after": after}, headers=shop.h(shop.sales)).json()["items"] == []


def test_rejected_and_held_orders(world):
    from decimal import Decimal

    shop = Shop(world, limit=Decimal("100000"))  # 400 bags × 350 = 1,40,000 → over the limit → on hold
    o = shop.order(shop.sales).json()
    assert o["status"] == "ON_HOLD"
    assert titles(shop, shop.sales) == []  # they booked it and saw "on hold" at once
    assert "On hold: Sharma Building Material" in titles(shop, shop.owner)  # admins release credit holds
    assert titles(shop, shop.ops) == []  # operations cannot release a credit hold
    shop.post(shop.owner, f"/orders/{o['id']}/reject", {"reason": "Clear old dues first"})
    t = inbox(shop, shop.sales)["items"][0]
    assert t["title"] == "Rejected: Sharma Building Material" and "Clear old dues first" in t["body"]


def test_whatsapp_to_dealer_on_delivery(world, monkeypatch):
    shop = Shop(world)
    sent = []
    monkeypatch.setattr(whatsapp_cloud, "send_template",
                        lambda to, name, lang, params: sent.append((to, name, params)) or "wamid.1")
    shop.dealer.tally_mobile = "98100 12345"
    shop.db.commit()

    o = shop.order(shop.sales, bags="300").json()
    shop.post(shop.ops, f"/orders/{o['id']}/approve")
    deliver(shop, o)
    assert sent == []  # off until the template is approved and its name entered

    world.db.add(SystemSetting(company_id=shop.co.id, key="whatsapp.delivery_template",
                               value={"v": "tayal_delivery_done"}))
    world.db.commit()
    o = shop.order(shop.sales, bags="200").json()
    shop.post(shop.ops, f"/orders/{o['id']}/approve")
    d = shop.post(shop.ops, "/delivery-orders", {
        "sales_order_id": o["id"], "godown_id": str(shop.g1.id), "vehicle_id": str(shop.truck.id),
        "driver_id": str(shop.driver.id), "send_now": True,
        "lines": [{"order_item_id": o["items"][0]["id"], "qty": "200"}]}).json()
    shop.post(shop.ops, f"/delivery-orders/{d['id']}/deliver", {"recipient_name": "Raju"})
    assert len(sent) == 1
    to, name, params = sent[0]
    assert to == "919810012345" and name == "tayal_delivery_done"
    assert params[0] == "Sharma Building Material" and params[1].startswith("200 bag")
    assert params[2] == "DL1LA1234" and params[3] == o["order_no"] and params[5] == "Raju"
    msg = shop.db.execute(select(DealerMessage).where(DealerMessage.kind == "DELIVERY")).scalar_one()
    assert msg.status == "SENT" and "200 bag" in msg.body

    # sending again for the same truck does nothing
    from app.modules.collections import delivery_msg

    assert delivery_msg.send_for_do(shop.db, msg.ref_id) is None and len(sent) == 1


def test_delivery_whatsapp_failure_is_logged_not_raised(world, monkeypatch):
    shop = Shop(world)

    def fail(*a):
        raise whatsapp_cloud.WhatsAppError("template not approved")

    monkeypatch.setattr(whatsapp_cloud, "send_template", fail)
    shop.dealer.tally_mobile = "9810012345"
    world.db.add(SystemSetting(company_id=shop.co.id, key="whatsapp.delivery_template", value={"v": "x"}))
    world.db.commit()
    o = shop.order(shop.sales, bags="300").json()
    shop.post(shop.ops, f"/orders/{o['id']}/approve")
    deliver(shop, o)  # delivery still succeeds
    msg = shop.db.execute(select(DealerMessage).where(DealerMessage.kind == "DELIVERY")).scalar_one()
    assert msg.status == "FAILED" and "not approved" in msg.error
