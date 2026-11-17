import re

from app.core.permissions import ROLE_DEFAULTS
from app.main import app


def test_credit_override_is_admin_only():
    """Decision D4: held orders are approved only by admin."""
    holders = {code for code, (_, perms) in ROLE_DEFAULTS.items() if "credit.override" in perms}
    assert holders == {"SUPER_ADMIN", "BUSINESS_OWNER"}


def test_backend_rejects_action_without_permission(world, client):
    c = world.company()
    sales = world.user(c, ["SALES_EXECUTIVE"])
    admin = world.user(c, ["SUPER_ADMIN"])
    body = {"registration_no": "DL1LA1234", "vehicle_type": "TRUCK_10W", "capacity_kg": "16000"}
    r = client.post("/api/v1/vehicles", json=body, headers=world.headers(sales, c))
    assert r.status_code == 403 and "masters.transport_manage" in r.json()["detail"]
    r = client.post("/api/v1/vehicles", json=body, headers=world.headers(admin, c))
    assert r.status_code == 201 and r.json()["ownership"] == "OWN"


def test_no_privilege_escalation_when_granting_roles(world, client):
    c = world.company()
    ops = world.user(c, ["OPERATIONS_MANAGER", "SUPER_ADMIN"])
    # an Ops+Admin can grant; now make a user-admin without super admin and try to grant SUPER_ADMIN
    r = client.post("/api/v1/users", headers=world.headers(ops, c),
                    json={"phone": "9000000001", "full_name": "HR", "password": "password-1", "roles": ["ACCOUNTS_EXECUTIVE"]})
    assert r.status_code == 201
    from sqlalchemy import select

    from app.modules.identity.models import Permission, Role, RolePermission

    # custom role with only admin.users
    db = world.db
    role = Role(code="USER_ADMIN", name="User admin")
    db.add(role)
    db.flush()
    assert db.get(Permission, "admin.users")
    db.add(RolePermission(role_id=role.id, permission_code="admin.users"))
    db.commit()
    limited = world.user(c, ["USER_ADMIN"])
    r = client.post("/api/v1/users", headers=world.headers(limited, c),
                    json={"phone": "9000000002", "full_name": "X", "password": "password-1", "roles": ["SUPER_ADMIN"]})
    assert r.status_code == 403
    assert db.execute(select(Role).where(Role.code == "SUPER_ADMIN")).scalar_one()


def test_user_without_role_in_company_is_rejected(world, client):
    a, b = world.company("AAA"), world.company("BBB")
    u = world.user(a, ["SUPER_ADMIN"])
    tokens = world.login(u)
    r = client.get("/api/v1/godowns", headers={"Authorization": f"Bearer {tokens['access_token']}",
                                               "X-Company-Id": str(b.id)})
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_FORBIDDEN"


def test_records_of_other_company_are_invisible(world, client):
    a, b = world.company("AAA"), world.company("BBB")
    ua, ub = world.user(a, ["SUPER_ADMIN"]), world.user(b, ["SUPER_ADMIN"])
    hb = world.headers(ub, b)
    gid = str(world.godown(b, "B godown").id)
    ha = world.headers(ua, a)
    assert client.get(f"/api/v1/godowns/{gid}", headers=ha).status_code == 404
    assert client.patch(f"/api/v1/godowns/{gid}", json={"name": "hijack"}, headers=ha).status_code == 404
    assert client.get("/api/v1/godowns", headers=ha).json()["total"] == 0
    assert client.get("/api/v1/godowns", headers=hb).json()["total"] == 1


def test_every_company_scoped_list_endpoint_rejects_foreign_company(world, client):
    """Isolation sweep over the whole API: any GET without path params that takes X-Company-Id must refuse
    a company the caller has no role in. New endpoints are covered automatically."""
    a, b = world.company("AAA"), world.company("BBB")
    u = world.user(a, ["SUPER_ADMIN"])
    token = world.login(u)["access_token"]
    spec = app.openapi()
    checked = 0
    for path, ops in spec["paths"].items():
        get = ops.get("get")
        if not get or re.search(r"\{", path):
            continue
        if not any(p.get("name") == "X-Company-Id" for p in get.get("parameters", [])):
            continue
        r = client.get(path, headers={"Authorization": f"Bearer {token}", "X-Company-Id": str(b.id)})
        assert r.status_code == 403, f"{path} returned {r.status_code}"
        checked += 1
    assert checked >= 10


def test_godown_supervisor_sees_only_assigned_godowns(world, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    h = world.headers(admin, c)
    g1, g2 = str(world.godown(c, "One").id), str(world.godown(c, "Two").id)
    assert client.get("/api/v1/godowns", headers=h).json()["total"] == 2
    sup = world.user(c, ["GODOWN_SUPERVISOR"], godown_ids=[g1])
    hs = world.headers(sup, c)
    items = client.get("/api/v1/godowns", headers=hs).json()["items"]
    assert [i["id"] for i in items] == [g1]
    assert client.get(f"/api/v1/godowns/{g2}", headers=hs).status_code == 404


def test_validation_errors_are_problem_json(world, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    r = client.post("/api/v1/vehicles", json={"registration_no": "DL 01 AB 1234", "vehicle_type": "BOAT",
                                              "capacity_kg": "-5"}, headers=world.headers(admin, c))
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    fields = {e["field"] for e in r.json()["errors"]}
    assert {"vehicle_type", "capacity_kg"} <= fields
