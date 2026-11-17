"""Orders and dispatch end to end (docs/06 §5 scenarios A, C and F, adapted to the pre-inventory phase)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.integrations.tally.xml_client import parse_balance
from app.modules.masters.models import (
    CompanyProduct,
    Dealer,
    DealerAssignment,
    DealerRate,
    Driver,
    Product,
    Vehicle,
)
from app.modules.masters.schemas import TallyLedgerIn
from app.modules.masters.service import upsert_dealers_from_tally
from app.modules.platform.models import SystemSetting

API = "/api/v1"


class Shop:
    """A small company: godown, products, a dealer with a rate, a truck, a driver, and people in each role."""

    def __init__(self, world, limit: Decimal | None = None, balance: Decimal = Decimal("0"),
                 balance_age: timedelta = timedelta(minutes=5)):
        self.w, db = world, world.db
        self.db = db
        self.co = world.company()
        self.g1 = world.godown(self.co, "Nasirpur")
        self.g2 = world.godown(self.co, "Uttam Nagar")
        world.catalog(self.co)
        self.ppc = db.execute(select(Product).where(Product.code == "AMB-PPC")).scalar_one()
        self.tmt = db.execute(select(Product).where(Product.code == "KAM-FE550D-12")).scalar_one()
        for cp in db.execute(select(CompanyProduct).where(CompanyProduct.company_id == self.co.id)).scalars():
            cp.erp_enabled = True
        upsert_dealers_from_tally(db, self.co.id, [
            TallyLedgerIn(guid="L1", name="Sharma Building Material", parent="Sundry Debtors", balance=balance),
            TallyLedgerIn(guid="L2", name="Gupta Traders", parent="Sundry Debtors", balance=Decimal("0")),
        ], None)
        self.dealer = db.execute(select(Dealer).where(Dealer.tally_ledger_guid == "L1")).scalar_one()
        self.other = db.execute(select(Dealer).where(Dealer.tally_ledger_guid == "L2")).scalar_one()
        self.dealer.credit_limit = limit
        self.dealer.tally_balance_at = datetime.now(UTC) - balance_age
        db.add(DealerRate(company_id=self.co.id, dealer_id=self.dealer.id, product_id=self.ppc.id, uom="BAG",
                          rate=Decimal("350"), tolerance_pct=Decimal("2"), valid_from=date.today() - timedelta(days=1)))
        self.truck = Vehicle(company_id=self.co.id, registration_no="DL1LA1234", vehicle_type="TRUCK_10W",
                             capacity_kg=Decimal("16000"))
        self.small = Vehicle(company_id=self.co.id, registration_no="DL1LB0001", vehicle_type="TRUCK_6W",
                             capacity_kg=Decimal("9000"))
        db.add_all([self.truck, self.small])
        db.flush()
        self.driver = Driver(company_id=self.co.id, name="Ramesh", phone="9810000001")
        db.add(self.driver)
        db.commit()

        self.sales_emp = world.employee(self.co, "S1", "Suresh")
        self.sales = world.user(self.co, ["SALES_EXECUTIVE"], employee=self.sales_emp)
        db.add(DealerAssignment(dealer_id=self.dealer.id, employee_id=self.sales_emp.id, kind="SALES",
                                valid_from=date.today()))
        self.so_emp = world.employee(self.co, "S2", "Sanjay")
        self.sales_ops = world.user(self.co, ["SALES_EXECUTIVE", "OPERATIONS_MANAGER"], employee=self.so_emp)
        self.ops = world.user(self.co, ["OPERATIONS_MANAGER"])
        self.owner = world.user(self.co, ["BUSINESS_OWNER"])
        self.sup = world.user(self.co, ["GODOWN_SUPERVISOR"], godown_ids=[self.g2.id])
        db.commit()
        self._h = {}

    def h(self, user):
        if user.id not in self._h:
            self._h[user.id] = self.w.headers(user, self.co)
        return self._h[user.id]

    def order(self, user, bags="400", rate=None, dealer=None, **extra):
        line = {"product_id": str(self.ppc.id), "qty": bags, "uom": "BAG", "godown_id": str(self.g1.id)}
        if rate is not None:
            line["rate"] = rate
        body = {"dealer_id": str((dealer or self.dealer).id), "lines": [line], **extra}
        return self.w.client.post(f"{API}/orders", json=body, headers=self.h(user))

    def post(self, user, path, body=None):
        return self.w.client.post(f"{API}{path}", json=body or {}, headers=self.h(user))

    def plan(self, user, order, bags, vehicle=None, **extra):
        body = {"sales_order_id": order["id"], "godown_id": str(self.g1.id),
                "vehicle_id": str((vehicle or self.truck).id), "driver_id": str(self.driver.id),
                "lines": [{"order_item_id": order["items"][0]["id"], "qty": bags}], **extra}
        return self.post(user, "/delivery-orders", body)


@pytest.fixture
def shop(world):
    return Shop(world)


def test_sales_books_ops_approves_and_dispatches_in_two_trucks(shop):
    r = shop.order(shop.sales)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["status"] == "UNDER_REVIEW"  # auto-approve is off by default
    assert o["order_no"].startswith("ITD/SO/")
    assert o["items"][0]["rate"] == "350.0000" and o["total_amount"] == "140000.00"
    assert o["items"][0]["base_qty"] == "400.000000"

    # Sales books only: no approval, no dispatch.
    assert shop.post(shop.sales, f"/orders/{o['id']}/approve").status_code == 403
    assert shop.plan(shop.sales, o, "100").status_code == 403

    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    assert o["status"] == "APPROVED"

    d1 = shop.plan(shop.ops, o, "300")
    assert d1.status_code == 201, d1.text
    d1 = d1.json()
    assert d1["planned_weight_kg"] == "15000.000" and d1["do_no"].startswith("ITD/DO/")
    # Only 100 bags left to plan now.
    over = shop.plan(shop.ops, o, "101", vehicle=shop.small)
    assert over.status_code == 422 and over.json()["code"] == "QTY_EXCEEDS_ORDER"

    d1 = shop.post(shop.ops, f"/delivery-orders/{d1['id']}/load", {}).json()
    assert d1["status"] == "LOADED"
    d1 = shop.post(shop.ops, f"/delivery-orders/{d1['id']}/dispatch", {}).json()
    assert d1["status"] == "DISPATCHED"
    o = shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()
    assert o["status"] == "PARTIALLY_DISPATCHED"
    assert o["items"][0]["dispatched_qty"] == "300.000" and o["items"][0]["remaining_qty"] == "100.000"
    shop.db.expire_all()
    assert shop.db.get(Vehicle, shop.truck.id).status == "ON_TRIP"

    d1 = shop.post(shop.ops, f"/delivery-orders/{d1['id']}/deliver", {"recipient_name": "Sharma ji"}).json()
    assert d1["status"] == "DELIVERED"
    shop.db.expire_all()
    assert shop.db.get(Vehicle, shop.truck.id).status == "AVAILABLE"

    d2 = shop.plan(shop.ops, o, "100", vehicle=shop.small).json()
    shop.post(shop.ops, f"/delivery-orders/{d2['id']}/dispatch", {})
    d2 = shop.post(shop.ops, f"/delivery-orders/{d2['id']}/deliver", {
        "recipient_name": "Munshi", "lines": [{"do_item_id": d2["items"][0]["id"], "delivered_qty": "98",
                                               "damaged_qty": "2"}]}).json()
    assert d2["status"] == "PARTIALLY_DELIVERED"
    o = shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()
    assert o["status"] == "DELIVERED"
    assert o["items"][0]["delivered_qty"] == "398.000"
    assert [d["status"] for d in o["delivery_orders"]] == ["DELIVERED", "PARTIALLY_DELIVERED"]
    assert "close" in o["can"]
    o = shop.post(shop.ops, f"/orders/{o['id']}/close", {"reason": "All supplied"}).json()
    assert o["status"] == "CLOSED"


def test_sales_sees_only_assigned_dealers(shop):
    r = shop.order(shop.sales, dealer=shop.other, rate="340")
    assert r.status_code == 404
    mine = shop.order(shop.sales).json()
    lst = shop.w.client.get(f"{API}/orders", headers=shop.h(shop.sales)).json()
    assert [o["id"] for o in lst["items"]] == [mine["id"]]
    # Operations sees all dealers' orders.
    shop.order(shop.ops, dealer=shop.other, rate="340")
    assert shop.w.client.get(f"{API}/orders", headers=shop.h(shop.ops)).json()["total"] == 2


def test_sales_plus_operations_books_and_plans_but_not_self_approves(shop):
    o = shop.order(shop.sales_ops, dealer=shop.other, rate="340").json()
    r = shop.post(shop.sales_ops, f"/orders/{o['id']}/approve", {})
    assert r.status_code == 403 and r.json()["code"] == "SELF_APPROVAL"
    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    d = shop.plan(shop.sales_ops, o, "200")
    assert d.status_code == 201, d.text
    assert shop.post(shop.sales_ops, f"/delivery-orders/{d.json()['id']}/dispatch", {}).json()["status"] == "DISPATCHED"


def test_credit_hold_needs_admin_override(world):
    shop = Shop(world, limit=Decimal("100000"), balance=Decimal("50000"))  # Tally: Dr 50,000
    shop.db.expire_all()
    assert shop.db.get(Dealer, shop.dealer.id).tally_balance == Decimal("50000.00")
    o = shop.order(shop.sales).json()  # 140,000 > 100,000 - 50,000
    assert o["status"] == "ON_HOLD" and o["hold_kind"] == "CREDIT"
    assert o["credit_checks"][0]["available_credit"] == "50000.00"
    assert any(r["code"] == "OVER_LIMIT" for r in o["review_reasons"])

    r = shop.post(shop.ops, f"/orders/{o['id']}/approve", {})
    assert r.status_code == 403 and r.json()["code"] == "ADMIN_APPROVAL_REQUIRED"
    r = shop.post(shop.owner, f"/orders/{o['id']}/approve", {})
    assert r.status_code == 409 and r.json()["code"] == "CREDIT_HOLD"
    r = shop.post(shop.owner, f"/orders/{o['id']}/approve", {"override_credit": True})
    assert r.status_code == 422 and r.json()["code"] == "REASON_REQUIRED"
    o = shop.post(shop.owner, f"/orders/{o['id']}/approve",
                  {"override_credit": True, "reason": "Cheque of 1 lakh received"}).json()
    assert o["status"] == "APPROVED" and o["approved_by_name"]
    # A dispatch of a held order is impossible; this one is approved so it can be planned.
    assert shop.plan(shop.ops, o, "10").status_code == 201


def test_open_orders_count_towards_exposure(world):
    shop = Shop(world, limit=Decimal("200000"))
    o1 = shop.order(shop.sales).json()
    shop.post(shop.ops, f"/orders/{o1['id']}/approve", {})
    o2 = shop.order(shop.sales, bags="300", confirm_duplicate=True).json()  # 105,000 > 200,000 - 140,000
    assert o2["status"] == "ON_HOLD"
    assert o2["credit_checks"][0]["open_order_exposure"] == "140000.00"


def test_stale_tally_balance_holds_when_limit_set(world):
    shop = Shop(world, limit=Decimal("10000000"), balance_age=timedelta(days=2))
    o = shop.order(shop.sales).json()
    assert o["status"] == "ON_HOLD" and o["hold_kind"] == "STALE_BALANCE"


def test_no_limit_is_not_credit_checked_by_default(world):
    shop = Shop(world, limit=None, balance_age=timedelta(days=30))
    assert shop.order(shop.sales).json()["status"] == "UNDER_REVIEW"


def test_rate_below_minimum_needs_admin(shop):
    o = shop.order(shop.sales, rate="340").json()  # 350 − 2 % = 343 minimum
    assert o["status"] == "UNDER_REVIEW"
    assert o["review_reasons"][0]["code"] == "RATE_BELOW_MIN"
    r = shop.post(shop.ops, f"/orders/{o['id']}/approve", {})
    assert r.status_code == 403 and r.json()["code"] == "RATE_APPROVAL_REQUIRED"
    assert shop.post(shop.owner, f"/orders/{o['id']}/approve", {}).json()["status"] == "APPROVED"


def test_rate_required_when_dealer_has_none(shop):
    r = shop.order(shop.ops, dealer=shop.other)
    assert r.status_code == 422 and r.json()["code"] == "RATE_REQUIRED"


def test_rate_in_another_unit_and_preview(shop):
    body = {"dealer_id": str(shop.dealer.id),
            "lines": [{"product_id": str(shop.ppc.id), "qty": "10", "uom": "MT"}]}
    p = shop.w.client.post(f"{API}/orders/preview", json=body, headers=shop.h(shop.sales)).json()
    line = p["lines"][0]
    assert line["base_qty"] == "200.000000" and line["rate_uom"] == "BAG" and line["amount"] == "70000.00"
    assert p["total_weight_kg"] == "10000.000"
    assert p["credit"]["result"] == "PASS"
    assert shop.w.client.get(f"{API}/orders", headers=shop.h(shop.sales)).json()["total"] == 0  # nothing saved


def test_duplicate_order_needs_confirmation(shop):
    shop.order(shop.sales)
    r = shop.order(shop.sales)
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_ORDER"
    assert shop.order(shop.sales, confirm_duplicate=True).status_code == 201


def test_draft_edit_and_submit(shop):
    d = shop.order(shop.sales, submit=False).json()
    assert d["status"] == "DRAFT" and d["order_no"] is None
    body = {"dealer_id": str(shop.dealer.id), "version": d["version"], "submit": True,
            "lines": [{"product_id": str(shop.ppc.id), "qty": "50", "uom": "BAG"}]}
    r = shop.w.client.put(f"{API}/orders/{d['id']}", json=body, headers=shop.h(shop.sales))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "UNDER_REVIEW" and r.json()["total_amount"] == "17500.00"
    body["version"] = d["version"]
    assert shop.w.client.put(f"{API}/orders/{d['id']}", json=body, headers=shop.h(shop.sales)).status_code == 409


def test_blocked_dealer_cannot_order(shop):
    shop.dealer.status, shop.dealer.blocked_reason = "BLOCKED", "Doubtful debt"
    shop.db.commit()
    r = shop.order(shop.sales)
    assert r.status_code == 422 and r.json()["code"] == "DEALER_BLOCKED"


def test_capacity_check_and_override(shop):
    o = shop.order(shop.sales).json()
    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    r = shop.plan(shop.ops, o, "200", vehicle=shop.small)  # 10,000 kg on a 9,000 kg truck (+5 %)
    assert r.status_code == 422 and r.json()["code"] == "OVER_CAPACITY"
    r = shop.plan(shop.ops, o, "200", vehicle=shop.small, capacity_override_reason="Short trip, checked axle")
    assert r.status_code == 201
    # Two DOs on one truck: the second is counted on top of the first.
    r = shop.plan(shop.ops, o, "190", vehicle=shop.truck)
    assert r.status_code == 201
    loads = {v["registration_no"]: v for v in
             shop.w.client.get(f"{API}/dispatch/vehicles", headers=shop.h(shop.ops)).json()}
    assert loads["DL1LA1234"]["planned_kg"] == "9500.000"


def test_pending_board_and_cancel_rules(shop):
    o = shop.order(shop.sales).json()
    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    d = shop.plan(shop.ops, o, "150").json()
    pend = shop.w.client.get(f"{API}/dispatch/pending", headers=shop.h(shop.ops)).json()
    assert pend[0]["remaining_qty"] == "250.000" and pend[0]["planned_qty"] == "150.000"
    r = shop.post(shop.ops, f"/orders/{o['id']}/cancel", {"reason": "Dealer changed mind"})
    assert r.status_code == 409 and r.json()["code"] == "ORDER_HAS_DISPATCH"
    shop.post(shop.ops, f"/delivery-orders/{d['id']}/cancel", {"reason": "Truck broke down"})
    assert shop.post(shop.ops, f"/orders/{o['id']}/cancel", {"reason": "Dealer changed mind"}).json()["status"] == "CANCELLED"
    assert shop.w.client.get(f"{API}/dispatch/pending", headers=shop.h(shop.ops)).json() == []


def test_hold_blocks_dispatch(shop):
    o = shop.order(shop.sales).json()
    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    d = shop.plan(shop.ops, o, "100").json()
    shop.post(shop.ops, f"/orders/{o['id']}/hold", {"reason": "Payment dispute"})
    r = shop.post(shop.ops, f"/delivery-orders/{d['id']}/dispatch", {})
    assert r.status_code == 409 and r.json()["code"] == "ORDER_NOT_DISPATCHABLE"


def test_dispatch_needs_driver_and_pod_must_add_up(shop):
    o = shop.order(shop.sales).json()
    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    d = shop.plan(shop.ops, o, "100", driver_id=None).json()
    r = shop.post(shop.ops, f"/delivery-orders/{d['id']}/dispatch", {})
    assert r.status_code == 422 and r.json()["code"] == "DRIVER_REQUIRED"
    d = shop.post(shop.ops, f"/delivery-orders/{d['id']}/dispatch", {"driver_id": str(shop.driver.id)}).json()
    r = shop.post(shop.ops, f"/delivery-orders/{d['id']}/deliver", {
        "recipient_name": "X", "lines": [{"do_item_id": d["items"][0]["id"], "delivered_qty": "90"}]})
    assert r.status_code == 422 and r.json()["code"] == "POD_MISMATCH"


def test_short_loading_leaves_remainder_on_order(shop):
    o = shop.order(shop.sales).json()
    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    d = shop.plan(shop.ops, o, "300").json()
    shop.post(shop.ops, f"/delivery-orders/{d['id']}/load", {"lines": [{"do_item_id": d["items"][0]["id"], "qty": "280"}]})
    shop.post(shop.ops, f"/delivery-orders/{d['id']}/dispatch", {})
    o = shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()
    assert o["items"][0]["dispatched_qty"] == "280.000" and o["items"][0]["remaining_qty"] == "120.000"


def test_godown_supervisor_only_handles_own_godown(shop):
    o = shop.order(shop.sales).json()
    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    d = shop.plan(shop.ops, o, "100").json()  # from Nasirpur; supervisor has Uttam Nagar only
    assert shop.post(shop.sup, f"/delivery-orders/{d['id']}/load", {}).status_code == 404
    lst = shop.w.client.get(f"{API}/delivery-orders", headers=shop.h(shop.sup)).json()
    assert lst["total"] == 0


def test_settings_can_switch_on_auto_approve(shop):
    shop.db.add(SystemSetting(company_id=shop.co.id, key="orders.auto_approve", value={"v": True}))
    shop.db.commit()
    assert shop.order(shop.sales).json()["status"] == "APPROVED"
    assert shop.order(shop.sales, rate="300", confirm_duplicate=True).json()["status"] == "UNDER_REVIEW"


@pytest.mark.parametrize("raw,expected", [
    ("-15000.00", Decimal("15000.00")),      # Dr: dealer owes us
    ("2500.50", Decimal("-2500.50")),        # Cr: advance from dealer
    ("15,000.00 Dr", Decimal("15000.00")),
    ("1,200.00 Cr", Decimal("-1200.00")),
    ("", Decimal("0")),
    (None, None),
])
def test_tally_balance_sign(raw, expected):
    assert parse_balance(raw) == expected


def test_tally_address_parsing_old_and_new_layouts():
    from app.modules.integrations.tally.xml_client import parse_ledgers

    old = """<ENVELOPE><LEDGER NAME="A"><GUID>g1</GUID><PARENT>Sundry Debtors</PARENT>
      <ADDRESS.LIST TYPE="String"><ADDRESS>Shop 12, Main Road</ADDRESS><ADDRESS>Najafgarh</ADDRESS></ADDRESS.LIST>
      <LEDSTATENAME>Delhi</LEDSTATENAME><PINCODE>110043</PINCODE></LEDGER></ENVELOPE>"""
    new = """<ENVELOPE><LEDGER NAME="B"><GUID>g2</GUID><PARENT>Sundry Debtors</PARENT>
      <LEDMAILINGDETAILS.LIST><ADDRESS.LIST TYPE="String"><ADDRESS>Plot 5, Sector 9</ADDRESS></ADDRESS.LIST>
      <STATE>Haryana</STATE><PINCODE>122001</PINCODE></LEDMAILINGDETAILS.LIST></LEDGER></ENVELOPE>"""
    none = """<ENVELOPE><LEDGER NAME="C"><GUID>g3</GUID><PARENT>Sundry Debtors</PARENT></LEDGER></ENVELOPE>"""
    assert parse_ledgers(old)[0].address == "Shop 12, Main Road, Najafgarh, Delhi 110043"
    assert parse_ledgers(new)[0].address == "Plot 5, Sector 9, Haryana 122001"
    assert parse_ledgers(none)[0].address is None


def test_order_shows_delivery_location(shop):
    from app.modules.masters.models import DealerAddress

    o = shop.order(shop.sales).json()
    assert o["delivery_location"] is None
    shop.dealer.tally_address = "Shop 12, Main Road, Najafgarh"
    shop.db.commit()
    o = shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()
    assert (o["delivery_location"], o["delivery_location_source"]) == ("Shop 12, Main Road, Najafgarh", "tally")
    site = DealerAddress(dealer_id=shop.dealer.id, kind="SITE", label="Dwarka site", line1="Sector 21", city="Delhi")
    shop.db.add(site)
    shop.db.commit()
    o = shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()
    assert (o["delivery_location"], o["delivery_location_source"]) == ("Dwarka site: Sector 21, Delhi", "dealer")
    o2 = shop.order(shop.sales, bags="10", delivery_address_id=str(site.id), confirm_duplicate=True).json()
    assert o2["delivery_location_source"] == "order"
    o2 = shop.post(shop.owner, f"/orders/{o2['id']}/approve", {}).json()
    d = shop.plan(shop.ops, o2, "10").json()
    assert d["delivery_address"] == "Dwarka site: Sector 21, Delhi"


def test_approver_sees_dealers_balance_now(world):
    """The order shows the dealer's current Tally balance, other open orders and the limit, for the approver."""
    shop = Shop(world, limit=Decimal("200000"), balance=Decimal("50000"))
    first = shop.post(shop.ops, f"/orders/{shop.order(shop.sales, bags='200').json()['id']}/approve", {}).json()
    o = shop.order(shop.sales, bags="300").json()  # 300 × 350 = 105,000
    c = shop.w.client.get(f"{API}/orders/{o['id']}", headers=shop.h(shop.ops)).json()["credit_now"]
    assert c["tally_balance"] == "50000.00" and c["credit_limit"] == "200000.00"
    assert c["open_order_exposure"] == first["total_amount"] == "70000.00"
    assert c["this_order_amount"] == "105000.00" and c["total_after"] == "225000.00"
