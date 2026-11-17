from sqlalchemy import select

from app.core.sequences import fiscal_year, next_number
from app.modules.platform.models import AuditLog


def test_every_master_change_is_audited(world, db, client):
    c = world.company()
    admin = world.user(c, ["SUPER_ADMIN"])
    h = world.headers(admin, c)
    vid = client.post("/api/v1/vehicles", json={"registration_no": "DL1LA1", "vehicle_type": "TRUCK_6W",
                                                "capacity_kg": "9000"}, headers=h).json()["id"]
    client.patch(f"/api/v1/vehicles/{vid}", json={"capacity_kg": "10000"}, headers=h)
    rows = db.execute(select(AuditLog).where(AuditLog.entity_id == vid).order_by(AuditLog.id)).scalars().all()
    assert [r.action for r in rows] == ["vehicle.create", "vehicle.update"]
    assert rows[1].before == {"capacity_kg": "9000.00"} and rows[1].after == {"capacity_kg": "10000.00"}
    assert rows[1].actor_id == admin.id
    log = client.get("/api/v1/audit", params={"entity_id": vid}, headers=h).json()["items"]
    assert len(log) == 2


def test_settings_have_defaults_and_typed_overrides(world, client):
    c = world.company()
    h = world.headers(world.user(c, ["SUPER_ADMIN"]), c)
    s = client.get("/api/v1/settings", headers=h).json()
    assert s["credit.stale_policy"] == "HOLD" and s["orders.default_tax_treatment"] == "NON_GST"
    assert client.put("/api/v1/settings/tally.stale_threshold_minutes", json={"value": "abc"}, headers=h).status_code == 422
    assert client.put("/api/v1/settings/tally.stale_threshold_minutes", json={"value": 60}, headers=h).status_code == 200
    assert client.get("/api/v1/settings", headers=h).json()["tally.stale_threshold_minutes"] == 60
    assert client.put("/api/v1/settings/nope", json={"value": 1}, headers=h).status_code == 422


def test_document_sequences_are_per_company_and_fy(world, db):
    from datetime import date

    a, b = world.company("AAA"), world.company("BBB")
    assert fiscal_year(date(2026, 11, 9)) == "2627" and fiscal_year(date(2027, 3, 31)) == "2627"
    assert fiscal_year(date(2027, 4, 1)) == "2728"
    assert [next_number(db, a.id, "SO") for _ in range(3)] == [1, 2, 3]
    assert next_number(db, b.id, "SO") == 1
    assert next_number(db, a.id, "SO", on=date(2027, 4, 1)) == 1


def test_health(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json()["checks"]["database"] == "ok"


def test_real_visitor_address_behind_cloudflare():
    from starlette.requests import Request

    from app.core.deps import client_ip

    def req(host, headers):
        return Request({"type": "http", "client": (host, 1), "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})

    # through the tunnel / local proxy: Cloudflare's header is used
    assert client_ip(req("127.0.0.1", {"cf-connecting-ip": "49.36.1.2", "x-forwarded-for": "10.0.0.1"})) == "49.36.1.2"
    assert client_ip(req("127.0.0.1", {"x-forwarded-for": "49.36.1.3, 127.0.0.1"})) == "49.36.1.3"
    # straight from the network: the header cannot be trusted
    assert client_ip(req("192.168.1.9", {"cf-connecting-ip": "1.2.3.4"})) == "192.168.1.9"


def test_api_docs_are_off_in_production(monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    s = get_settings()
    monkeypatch.setattr(s, "env", "production")
    monkeypatch.setattr(s, "jwt_secret", "x" * 40)
    c = TestClient(create_app())
    assert c.get("/api/v1/docs").status_code == 404 and c.get("/api/v1/openapi.json").status_code == 404
    assert c.get("/health/live").status_code == 200
