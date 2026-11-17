from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.modules.masters.models import Dealer, DealerAssignment, Product
from app.modules.masters.schemas import TallyLedgerIn
from app.modules.masters.service import upsert_dealers_from_tally
from app.modules.platform.models import AuditLog


def _ledgers(n=3, suffix=""):
    return [TallyLedgerIn(guid=f"g-{i}", name=f"Party {i}{suffix}", parent="Sundry Debtors",
                          credit_limit=Decimal("500000"), credit_period_days=15) for i in range(1, n + 1)]


def test_tally_upsert_is_idempotent_and_keeps_erp_fields(world, db):
    c = world.company()
    assert upsert_dealers_from_tally(db, c.id, _ledgers(), None) == {"created": 3, "updated": 0, "unchanged": 0}
    db.commit()
    d = db.execute(select(Dealer).where(Dealer.tally_ledger_guid == "g-1")).scalar_one()
    d.payment_terms_days, d.aliases = 20, ["Party One (site)"]
    db.commit()

    assert upsert_dealers_from_tally(db, c.id, _ledgers(), None) == {"created": 0, "updated": 0, "unchanged": 3}
    stats = upsert_dealers_from_tally(db, c.id, _ledgers(suffix=" (renamed)"), None)
    db.commit()
    assert stats["updated"] == 3
    db.refresh(d)
    assert d.name == "Party 1 (renamed)"  # Tally owns the name
    assert d.payment_terms_days == 20 and d.aliases == ["Party One (site)"]  # ERP fields untouched
    assert db.execute(select(AuditLog).where(AuditLog.action == "dealer.updated_from_tally")).first()


def test_dealers_cannot_be_created_by_hand(world, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    r = client.post("/api/v1/dealers", json={"name": "Manual"}, headers=world.headers(admin, c))
    assert r.status_code == 405


def test_dealer_update_uses_optimistic_locking(world, db, client):
    c = world.company()
    upsert_dealers_from_tally(db, c.id, _ledgers(1), None)
    db.commit()
    d = db.execute(select(Dealer)).scalar_one()
    admin = world.user(c, ["SUPER_ADMIN"])
    h = world.headers(admin, c)
    r = client.patch(f"/api/v1/dealers/{d.id}", json={"version": d.version, "credit_policy": "WARN"}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == d.version + 1
    r = client.patch(f"/api/v1/dealers/{d.id}", json={"version": d.version, "payment_terms_days": 30}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"


def test_sales_exec_sees_only_assigned_dealers(world, db, client):
    c = world.company()
    upsert_dealers_from_tally(db, c.id, _ledgers(3), None)
    db.commit()
    emp = world.employee(c)
    admin = world.user(c, ["SUPER_ADMIN"])
    sales = world.user(c, ["SALES_EXECUTIVE"], employee=emp)
    d1 = db.execute(select(Dealer).where(Dealer.tally_ledger_guid == "g-1")).scalar_one()
    r = client.post("/api/v1/dealers/assignments/bulk", headers=world.headers(admin, c),
                    json={"dealer_ids": [str(d1.id)], "employee_id": str(emp.id), "kind": "SALES"})
    assert r.status_code == 200
    hs = world.headers(sales, c)
    items = client.get("/api/v1/dealers", headers=hs).json()["items"]
    assert [i["id"] for i in items] == [str(d1.id)]
    other = db.execute(select(Dealer).where(Dealer.tally_ledger_guid == "g-2")).scalar_one()
    assert client.get(f"/api/v1/dealers/{other.id}", headers=hs).status_code == 404


def test_reassignment_keeps_history_with_one_open_row(world, db, client):
    c = world.company()
    upsert_dealers_from_tally(db, c.id, _ledgers(1), None)
    db.commit()
    d = db.execute(select(Dealer)).scalar_one()
    e1, e2 = world.employee(c, "E1"), world.employee(c, "E2")
    h = world.headers(world.user(c, ["SUPER_ADMIN"]), c)
    for e in (e1, e2):
        r = client.post("/api/v1/dealers/assignments/bulk", headers=h,
                        json={"dealer_ids": [str(d.id)], "employee_id": str(e.id), "kind": "COLLECTION",
                              "reason": "rebalance"})
        assert r.status_code == 200
    rows = db.execute(select(DealerAssignment).where(DealerAssignment.dealer_id == d.id)).scalars().all()
    assert len(rows) == 2
    assert [r.employee_id for r in rows if r.valid_to is None] == [e2.id]


def test_customer_rates_resolution_and_history(world, db, client):
    c = world.company()
    world.catalog()
    upsert_dealers_from_tally(db, c.id, _ledgers(1), None)
    db.commit()
    d = db.execute(select(Dealer)).scalar_one()
    p = db.execute(select(Product).where(Product.code == "AMB-PPC")).scalar_one()
    admin = world.user(c, ["SUPER_ADMIN"])
    h = world.headers(admin, c)
    g = str(world.godown(c, "Nasirpur").id)
    today = date.today()
    base = {"product_id": str(p.id), "uom": "BAG", "valid_from": str(today - timedelta(days=10))}
    assert client.post(f"/api/v1/dealers/{d.id}/rates", headers=h,
                       json={**base, "rate": "385", "tolerance_pct": "1"}).status_code == 201
    # price change today closes the previous open rate
    assert client.post(f"/api/v1/dealers/{d.id}/rates", headers=h,
                       json={**base, "rate": "390", "tolerance_pct": "1", "valid_from": str(today)}).status_code == 201
    assert client.post(f"/api/v1/dealers/{d.id}/rates", headers=h,
                       json={**base, "rate": "380", "godown_id": g}).status_code == 201

    r = client.get(f"/api/v1/dealers/{d.id}/rates/resolve", params={"product_id": str(p.id)}, headers=h).json()
    assert Decimal(r["rate"]) == 390 and Decimal(r["min_rate"]) == Decimal("386.1")
    r = client.get(f"/api/v1/dealers/{d.id}/rates/resolve", params={"product_id": str(p.id), "godown_id": g},
                   headers=h).json()
    assert Decimal(r["rate"]) == 380  # godown-specific rate is more specific
    r = client.get(f"/api/v1/dealers/{d.id}/rates/resolve",
                   params={"product_id": str(p.id), "on": str(today - timedelta(days=5))}, headers=h).json()
    assert Decimal(r["rate"]) == 385
    bad = client.post(f"/api/v1/dealers/{d.id}/rates", headers=h, json={**base, "rate": "1", "uom": "PC"})
    assert bad.status_code == 422


def test_same_day_rate_entry_corrects_instead_of_duplicating(world, db, client):
    c = world.company()
    world.catalog()
    upsert_dealers_from_tally(db, c.id, _ledgers(1), None)
    db.commit()
    d = db.execute(select(Dealer)).scalar_one()
    p = db.execute(select(Product).where(Product.code == "AMB-PPC")).scalar_one()
    h = world.headers(world.user(c, ["SUPER_ADMIN"]), c)
    body = {"product_id": str(p.id), "uom": "BAG", "valid_from": str(date.today()), "rate": "392"}
    first = client.post(f"/api/v1/dealers/{d.id}/rates", headers=h, json=body).json()
    second = client.post(f"/api/v1/dealers/{d.id}/rates", headers=h, json={**body, "rate": "395"}).json()
    assert first["id"] == second["id"] and Decimal(second["rate"]) == 395
    assert len(client.get(f"/api/v1/dealers/{d.id}/rates", headers=h).json()) == 1
    assert db.execute(select(AuditLog).where(AuditLog.action == "dealer_rate.correct")).scalar_one().before == {"rate": "392.0000"}


def test_users_become_employees_and_can_be_assigned_dealers(world, db, client):
    from app.modules.identity.models import User
    from app.modules.identity.service import backfill_employees

    c = world.company()
    upsert_dealers_from_tally(db, c.id, _ledgers(1), None)
    db.commit()
    admin = world.user(c, ["SUPER_ADMIN"])  # created before the rule: no employee record
    h = world.headers(admin, c)
    assert backfill_employees(db, c.id) == 1
    db.commit()

    r = client.post("/api/v1/users", headers=h, json={"phone": "9811111111", "full_name": "Ravi Collections",
                                                      "password": "password-1", "roles": ["COLLECTION_EXECUTIVE"]})
    assert r.status_code == 201 and r.json()["employee_id"]
    emps = client.get("/api/v1/employees", params={"active": True}, headers=h).json()["items"]
    assert {e["full_name"] for e in emps} == {"Test User", "Ravi Collections"}

    ravi_emp = r.json()["employee_id"]
    d = db.execute(select(Dealer)).scalar_one()
    r = client.post("/api/v1/dealers/assignments/bulk", headers=h,
                    json={"dealer_ids": [str(d.id)], "employee_id": ravi_emp, "kind": "COLLECTION"})
    assert r.status_code == 200
    # Ravi signs in and sees exactly that dealer
    ravi = db.execute(select(User).where(User.phone == "9811111111")).scalar_one()
    login = client.post("/api/v1/auth/login", json={"phone": "9811111111", "password": "password-1"}).json()
    hr = {"Authorization": f"Bearer {login['access_token']}", "X-Company-Id": str(c.id)}
    assert [x["name"] for x in client.get("/api/v1/dealers", headers=hr).json()["items"]] == [d.name]
    assert ravi.employee_id is not None


def test_dealer_search_matches_words_in_any_order_ignoring_punctuation(world):
    from app.modules.masters.schemas import TallyLedgerIn
    from app.modules.masters.service import upsert_dealers_from_tally

    co = world.company()
    upsert_dealers_from_tally(world.db, co.id, [
        TallyLedgerIn(guid="a", name="SONU B.M. (NAJAFGARH)", parent="Sundry Debtors"),
        TallyLedgerIn(guid="b", name="Sonu Traders", parent="Doubtful Debts"),
        TallyLedgerIn(guid="c", name="Monu Building Material", parent="Sundry Debtors"),
    ], None)
    world.db.commit()
    h = world.headers(world.user(co, ["OPERATIONS_MANAGER"]), co)

    def names(q, **extra):
        r = world.client.get("/api/v1/dealers", params={"q": q, **extra}, headers=h)
        assert r.status_code == 200, r.text
        return sorted(d["name"] for d in r.json()["items"])

    assert names("sonu bm") == ["SONU B.M. (NAJAFGARH)"]
    assert names("najafgarh sonu") == ["SONU B.M. (NAJAFGARH)"]
    assert names("sonu") == ["SONU B.M. (NAJAFGARH)", "Sonu Traders"]  # blocked ones are found too
    assert names("sonu", status="ACTIVE") == ["SONU B.M. (NAJAFGARH)"]
    assert names("b.m.") == ["SONU B.M. (NAJAFGARH)"]
    assert names("100%") == []
