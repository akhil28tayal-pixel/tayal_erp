"""Password reset, reactivation and the rule that you cannot manage someone with more access than you."""

from tests.conftest import PASSWORD


def test_admin_resets_password_and_signs_user_out(world, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    sales = world.user(c, ["SALES_EXECUTIVE"])
    old = world.login(sales)
    h = world.headers(admin, c)

    r = client.post(f"/api/v1/users/{sales.id}/password", headers=h, json={"new_password": "short"})
    assert r.status_code == 422
    r = client.post(f"/api/v1/users/{sales.id}/password", headers=h, json={"new_password": "brand-new-pass"})
    assert r.status_code == 204, r.text

    # old session is gone, old password no longer works, the new one does
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old['access_token']}"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"phone": sales.phone, "password": PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"phone": sales.phone, "password": "brand-new-pass"}).status_code == 200


def test_reset_unlocks_a_locked_account(world, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    sales = world.user(c, ["SALES_EXECUTIVE"])
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"phone": sales.phone, "password": "wrong-password"})
    assert client.post("/api/v1/auth/login", json={"phone": sales.phone, "password": PASSWORD}).status_code == 423
    client.post(f"/api/v1/users/{sales.id}/password", headers=world.headers(admin, c), json={"new_password": "brand-new-pass"})
    assert client.post("/api/v1/auth/login", json={"phone": sales.phone, "password": "brand-new-pass"}).status_code == 200


def test_deactivated_user_can_be_reactivated(world, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    sales = world.user(c, ["SALES_EXECUTIVE"])
    h = world.headers(admin, c)
    assert client.post(f"/api/v1/users/{sales.id}/deactivate", headers=h).status_code == 200
    assert client.post("/api/v1/auth/login", json={"phone": sales.phone, "password": PASSWORD}).status_code == 401
    r = client.post(f"/api/v1/users/{sales.id}/activate", headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert client.post("/api/v1/auth/login", json={"phone": sales.phone, "password": PASSWORD}).status_code == 200


def test_owner_cannot_take_over_super_admin(world, client):
    """BUSINESS_OWNER manages users but has less than SUPER_ADMIN: resetting the super admin's password would let
    them sign in as the super admin, so it is refused, as are role changes and deactivation."""
    c = world.company()
    root = world.user(c, ["SUPER_ADMIN"])
    owner = world.user(c, ["BUSINESS_OWNER"])
    h = world.headers(owner, c)
    for r in (client.post(f"/api/v1/users/{root.id}/password", headers=h, json={"new_password": "taken-over-1"}),
              client.put(f"/api/v1/users/{root.id}/roles", headers=h, json={"roles": ["SALES_EXECUTIVE"]}),
              client.post(f"/api/v1/users/{root.id}/deactivate", headers=h)):
        assert r.status_code == 403, r.text
    # but the owner still manages ordinary staff
    sales = world.user(c, ["SALES_EXECUTIVE"])
    assert client.post(f"/api/v1/users/{sales.id}/password", headers=h, json={"new_password": "brand-new-pass"}).status_code == 204


def test_unknown_phone_and_wrong_password_look_the_same(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE"])
    a = client.post("/api/v1/auth/login", json={"phone": "9000000000", "password": "whatever-1"})
    b = client.post("/api/v1/auth/login", json={"phone": u.phone, "password": "whatever-1"})
    assert a.status_code == b.status_code == 401 and a.json()["detail"] == b.json()["detail"]


def test_api_responses_carry_security_headers(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE"])
    r = client.get("/api/v1/auth/me", headers=world.headers(u, c))
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
