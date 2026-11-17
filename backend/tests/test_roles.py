from app.core.permissions import ROLE_DEFAULTS
from app.modules.identity.service import backfill_employees


def _perms(client, world, user, company):
    login = client.post("/api/v1/auth/login", json={"phone": user.phone, "password": "secret-pass-1"}).json()
    return set(next(c for c in login["companies"] if c["company_id"] == str(company.id))["permissions"])


def test_sales_role_books_orders_only():
    sales = ROLE_DEFAULTS["SALES_EXECUTIVE"][1]
    assert "orders.create" in sales
    assert not {"payments.report", "orders.approve", "dispatch.plan", "dispatch.confirm", "credit.override"} & sales


def test_sales_plus_operations_can_book_orders_and_plan_dispatch(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE", "OPERATIONS_MANAGER"])
    perms = _perms(client, world, u, c)
    assert {"orders.create", "dispatch.plan", "dispatch.confirm", "masters.transport_manage"} <= perms
    plain = _perms(client, world, world.user(c, ["SALES_EXECUTIVE"]), c)
    assert "dispatch.plan" not in plain and "orders.create" in plain


def test_super_admin_assigns_and_changes_roles(world, db, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    h = world.headers(admin, c)
    g = world.godown(c, "Nasirpur")
    new = client.post("/api/v1/users", headers=h, json={"phone": "9822222222", "full_name": "Mohan",
                                                       "password": "password-1", "roles": ["SALES_EXECUTIVE"]}).json()
    listed = {u["full_name"]: u for u in client.get("/api/v1/users", headers=h).json()["items"]}
    assert listed["Mohan"]["roles"] == ["SALES_EXECUTIVE"]

    # add Operations on top of Sales
    r = client.put(f"/api/v1/users/{new['id']}/roles", headers=h,
                   json={"roles": ["SALES_EXECUTIVE", "OPERATIONS_MANAGER"]})
    assert r.status_code == 200 and r.json()["roles"] == ["OPERATIONS_MANAGER", "SALES_EXECUTIVE"]

    # godown supervisor (a restricted role) can be granted too, with godowns
    r = client.put(f"/api/v1/users/{new['id']}/roles", headers=h,
                   json={"roles": ["GODOWN_SUPERVISOR"], "godown_ids": [str(g.id)]})
    assert r.status_code == 200 and r.json()["godown_ids"] == [str(g.id)]

    # removing every role keeps the user listed (so roles can be given back)
    r = client.put(f"/api/v1/users/{new['id']}/roles", headers=h, json={"roles": []})
    assert r.status_code == 200 and r.json()["roles"] == []
    assert "Mohan" in {u["full_name"] for u in client.get("/api/v1/users", headers=h).json()["items"]}


def test_admin_cannot_lock_themselves_out(world, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    r = client.put(f"/api/v1/users/{admin.id}/roles", headers=world.headers(admin, c), json={"roles": ["SALES_EXECUTIVE"]})
    assert r.status_code == 422 and r.json()["code"] == "SELF_LOCKOUT"


def test_role_changes_in_code_reach_existing_databases(db):
    """seed-base removes permissions dropped from a system role (Sales lost payments.report)."""
    from sqlalchemy import select

    from app.cli import seed_base
    from app.modules.identity.models import Role, RolePermission

    sales = db.execute(select(Role).where(Role.code == "SALES_EXECUTIVE")).scalar_one()
    db.add(RolePermission(role_id=sales.id, permission_code="payments.report"))  # as in an older database
    db.commit()
    seed_base(db)
    db.commit()
    have = set(db.execute(select(RolePermission.permission_code).where(RolePermission.role_id == sales.id)).scalars())
    assert have == ROLE_DEFAULTS["SALES_EXECUTIVE"][1]
    assert backfill_employees  # imported helper still available
