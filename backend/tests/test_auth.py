from app.modules.identity.models import User
from tests.conftest import PASSWORD


def test_login_returns_tokens_and_company_permissions(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE"])
    body = world.login(u)
    assert body["access_token"] and body["refresh_token"]
    [access] = body["companies"]
    assert access["company_code"] == "ITD"
    assert access["roles"] == ["SALES_EXECUTIVE"]
    assert "orders.create" in access["permissions"]
    assert "credit.override" not in access["permissions"]


def test_wrong_password_and_lockout(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE"])
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"phone": u.phone, "password": "wrong-password"})
        assert r.status_code == 401
        assert r.json()["code"] == "INVALID_CREDENTIALS"
    r = client.post("/api/v1/auth/login", json={"phone": u.phone, "password": PASSWORD})
    assert r.status_code == 423
    assert r.json()["code"] == "ACCOUNT_LOCKED"


def test_unknown_phone_looks_like_wrong_password(client):
    r = client.post("/api/v1/auth/login", json={"phone": "0000000000", "password": "whatever1"})
    assert r.status_code == 401 and r.json()["code"] == "INVALID_CREDENTIALS"


def test_refresh_rotates_and_reuse_revokes_whole_session(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE"])
    first = world.login(u)["refresh_token"]

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert r.status_code == 200
    second = r.json()["refresh_token"]
    assert second != first

    # Replaying the already-rotated token = theft signal → family revoked.
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert r.status_code == 401 and r.json()["code"] == "REFRESH_TOKEN_REUSED"
    # The legitimate latest token is now dead too.
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": second})
    assert r.status_code == 401


def test_logout_revokes_refresh_token(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE"])
    tokens = world.login(u)
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=h).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_deactivated_user_access_token_stops_working(world, client, db):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    victim = world.user(c, ["SALES_EXECUTIVE"])
    victim_h = world.headers(victim, c)
    assert client.get("/api/v1/auth/me", headers=victim_h).status_code == 200

    r = client.post(f"/api/v1/users/{victim.id}/deactivate", headers=world.headers(admin, c))
    assert r.status_code == 200
    r = client.get("/api/v1/auth/me", headers=victim_h)
    assert r.status_code == 401
    db.expire_all()
    assert db.get(User, victim.id).is_active is False


def test_missing_and_garbage_tokens(client):
    assert client.get("/api/v1/auth/me").json()["code"] == "UNAUTHENTICATED"
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401


def test_password_change_revokes_sessions(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE"])
    tokens = world.login(u)
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    r = client.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": "new-pass-123"},
                    headers=h)
    assert r.status_code == 204
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    assert client.post("/api/v1/auth/login", json={"phone": u.phone, "password": "new-pass-123"}).status_code == 200


def test_web_cookie_mode_keeps_refresh_token_out_of_javascript(world, client):
    c = world.company()
    u = world.user(c, ["SALES_EXECUTIVE"])
    r = client.post("/api/v1/auth/login", json={"phone": u.phone, "password": PASSWORD, "use_cookie": True})
    assert r.status_code == 200 and r.json()["refresh_token"] is None
    assert "httponly" in r.headers["set-cookie"].lower() and "erp_rt=" in r.headers["set-cookie"]
    r2 = client.post("/api/v1/auth/refresh")  # cookie sent automatically by the client jar
    assert r2.status_code == 200 and r2.json()["access_token"]
    # the old cookie value is now rotated; a fresh cookie was set
    assert "erp_rt=" in r2.headers["set-cookie"]
    h = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 204
    assert client.post("/api/v1/auth/refresh").status_code == 401
