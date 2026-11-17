"""Changing the quantity while dispatching: fewer bags than ordered (the rest kept or closed) or more (order grows)."""

import pytest

from tests.test_orders_dispatch import API, Shop


@pytest.fixture
def shop(world):
    return Shop(world)


def approved(shop, bags):
    o = shop.order(shop.sales, bags=bags).json()
    return shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()


def get(shop, path):
    return shop.w.client.get(f"{API}{path}", headers=shop.h(shop.ops)).json()


def deliver(shop, d, **body):
    return shop.post(shop.ops, f"/delivery-orders/{d['id']}/deliver", {"recipient_name": "Munim", **body})


def test_delivered_more_than_sent_increases_the_order(shop):
    o = approved(shop, "100")
    d = shop.plan(shop.ops, o, "100", send_now=True).json()
    assert d["status"] == "DISPATCHED"
    line = [{"do_item_id": d["items"][0]["id"], "delivered_qty": "120"}]
    r = deliver(shop, d, lines=line)
    assert r.status_code == 422 and r.json()["code"] == "POD_MISMATCH"

    r = deliver(shop, d, lines=line, allow_more=True)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["delivered_qty"] == "120.000"
    o = get(shop, f"/orders/{o['id']}")
    it = o["items"][0]
    assert o["status"] == "DELIVERED" and it["qty"] == "120.000" and it["remaining_qty"] == "0.000"
    assert o["total_amount"] == "42000.00"  # 120 × 350
    hist = get(shop, f"/orders/{o['id']}/history")
    assert any(h["what"] == "Quantity changed at dispatch" and "100 → 120" in h["detail"] for h in hist)


def test_dealer_took_less_and_rest_came_back(shop):
    o = approved(shop, "60")
    d = shop.plan(shop.ops, o, "60", send_now=True).json()
    # 50 delivered, 10 must be accounted for
    r = deliver(shop, d, lines=[{"do_item_id": d["items"][0]["id"], "delivered_qty": "50"}])
    assert r.json()["code"] == "POD_MISMATCH"
    r = deliver(shop, d, lines=[{"do_item_id": d["items"][0]["id"], "delivered_qty": "50", "returned_qty": "10"}])
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["dispatched_qty"] == "50.000" and r.json()["status"] == "DELIVERED"
    o = get(shop, f"/orders/{o['id']}")
    assert o["items"][0]["remaining_qty"] == "10.000" and o["status"] == "PARTIALLY_DELIVERED"


def test_dealer_took_less_and_the_rest_is_closed(shop):
    o = approved(shop, "60")
    d = shop.plan(shop.ops, o, "60", send_now=True).json()
    r = deliver(shop, d, close_rest=True,
                lines=[{"do_item_id": d["items"][0]["id"], "delivered_qty": "50", "returned_qty": "10"}])
    assert r.status_code == 200, r.text
    o = get(shop, f"/orders/{o['id']}")
    assert o["status"] == "DELIVERED" and o["items"][0]["remaining_qty"] == "0.000"
    assert any(h["what"] == "Rest of the order closed" for h in get(shop, f"/orders/{o['id']}/history"))


def test_send_more_than_ordered_when_planning(shop):
    o = approved(shop, "100")
    r = shop.plan(shop.ops, o, "120", send_now=True)
    assert r.status_code == 422 and r.json()["code"] == "QTY_EXCEEDS_ORDER"
    r = shop.plan(shop.ops, o, "120", send_now=True, allow_more=True)
    assert r.status_code == 201, r.text
    o = get(shop, f"/orders/{o['id']}")
    assert o["items"][0]["qty"] == "120.000" and o["items"][0]["dispatched_qty"] == "120.000"
    assert o["status"] == "DISPATCHED"


def test_send_less_and_close_rest_when_planning(shop):
    o = approved(shop, "60")
    d = shop.plan(shop.ops, o, "50", send_now=True, close_rest=True).json()
    o = get(shop, f"/orders/{o['id']}")
    assert o["status"] == "DISPATCHED" and o["items"][0]["remaining_qty"] == "0.000"
    deliver(shop, d)
    assert get(shop, f"/orders/{o['id']}")["status"] == "DELIVERED"


def test_more_at_the_gate_than_planned(shop):
    o = approved(shop, "100")
    d = shop.plan(shop.ops, o, "100").json()
    line = [{"do_item_id": d["items"][0]["id"], "qty": "110"}]
    r = shop.post(shop.ops, f"/delivery-orders/{d['id']}/dispatch", {"lines": line})
    assert r.status_code == 422 and r.json()["code"] == "QTY_EXCEEDS_PLAN"
    r = shop.post(shop.ops, f"/delivery-orders/{d['id']}/dispatch", {"lines": line, "allow_more": True})
    assert r.status_code == 200, r.text
    o = get(shop, f"/orders/{o['id']}")
    assert o["items"][0]["qty"] == "110.000" and o["status"] == "DISPATCHED"


def test_less_at_the_gate_keeps_or_closes_the_rest(shop):
    o = approved(shop, "100")
    d = shop.plan(shop.ops, o, "100").json()
    line = [{"do_item_id": d["items"][0]["id"], "qty": "80"}]
    shop.post(shop.ops, f"/delivery-orders/{d['id']}/dispatch", {"lines": line, "close_rest": True})
    o = get(shop, f"/orders/{o['id']}")
    assert o["items"][0]["remaining_qty"] == "0.000" and o["status"] == "DISPATCHED"


def test_delivery_recorded_for_an_earlier_day(shop):
    from datetime import date, timedelta

    o = approved(shop, "100")
    d = shop.plan(shop.ops, o, "100", send_now=True).json()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert deliver(shop, d, delivered_on=tomorrow).status_code == 422
    two_days_ago = (date.today() - timedelta(days=2)).isoformat()
    r = deliver(shop, d, delivered_on=two_days_ago)
    assert r.status_code == 200, r.text
    x = r.json()
    assert x["delivered_at"].startswith(two_days_ago) and x["dispatch_date"] == two_days_ago
    assert x["dispatched_at"] <= x["delivered_at"]
    # the daily report shows it on that day, not today
    rep = get(shop, f"/reports/daily?day={two_days_ago}")
    assert rep["total"]["dealers"] == 1
    assert get(shop, f"/reports/daily?day={date.today().isoformat()}")["total"]["dealers"] == 0


def test_sent_out_on_an_earlier_day(shop):
    from datetime import date, timedelta

    o = approved(shop, "100")
    yday = (date.today() - timedelta(days=1)).isoformat()
    d = shop.plan(shop.ops, o, "100", send_now=True, dispatch_date=yday).json()
    assert d["dispatched_at"].startswith(yday)
    r = deliver(shop, d, delivered_on=yday)
    assert r.json()["delivered_at"].startswith(yday)
