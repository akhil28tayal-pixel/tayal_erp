"""The web "Sync with Tally" button: starts a background run, records the result, saves the Tally address."""

from decimal import Decimal

from sqlalchemy import select

from app.modules.integrations.tally import sync
from app.modules.integrations.tally.xml_client import TallyError
from app.modules.masters.models import Dealer
from app.modules.masters.schemas import TallyGodownIn, TallyLedgerIn
from app.modules.platform.settings import get_setting

API = "/api/v1"


class FakeTally:
    fail: str | None = None
    seen_url: str | None = None

    def __init__(self, url, timeout=60.0):
        FakeTally.seen_url = url

    def companies(self):
        if FakeTally.fail:
            raise TallyError(FakeTally.fail)
        return ["Ishika Traders Daily."]

    def ledgers(self, company):
        return [TallyLedgerIn(guid="L1", name="Sharma Building Material", parent="Sundry Debtors",
                              balance=Decimal("185000"), mobile="9810012345")]

    def godowns(self, company):
        return [TallyGodownIn(guid="G1", name="Nasirpur")]

    def stock_items(self, company):
        return []


def _setup(world, monkeypatch, roles=("ACCOUNTS_EXECUTIVE",)):
    monkeypatch.setattr(sync, "TallyClient", FakeTally)
    FakeTally.fail = None
    co = world.company()
    co.tally_company_name = "Ishika Traders Daily"
    world.db.commit()
    return co, world.headers(world.user(co, list(roles)), co)


def test_sync_button_runs_and_saves_address(world, monkeypatch):
    co, h = _setup(world, monkeypatch)
    first = world.client.post(f"{API}/tally/sync", json={}, headers=h)
    assert first.status_code == 422 and first.json()["code"] == "TALLY_URL_REQUIRED"

    r = world.client.post(f"{API}/tally/sync", json={"tally_url": "192.168.0.5"}, headers=h)
    assert r.status_code == 202, r.text
    assert FakeTally.seen_url == "http://192.168.0.5:9000"
    st = world.client.get(f"{API}/tally/sync", headers=h).json()  # TestClient runs the background task first
    assert st["latest"]["status"] == "SUCCESS", st
    assert st["latest"]["summary"]["tally"] == {"ledgers": 1, "godowns": 1, "stock_items": 0}
    assert st["tally_url"] == "http://192.168.0.5:9000" and st["last_success"]["triggered_by_name"]
    world.db.expire_all()
    d = world.db.execute(select(Dealer).where(Dealer.tally_ledger_guid == "L1")).scalar_one()
    assert d.tally_balance == Decimal("185000.00") and d.tally_mobile == "9810012345"
    assert get_setting(world.db, co.id, "tally.url") == "http://192.168.0.5:9000"
    # Next time the saved address is used.
    assert world.client.post(f"{API}/tally/sync", json={}, headers=h).status_code == 202


def test_failed_sync_is_reported(world, monkeypatch):
    co, h = _setup(world, monkeypatch)
    FakeTally.fail = "Could not reach Tally at http://10.0.0.9:9000"
    world.client.post(f"{API}/tally/sync", json={"tally_url": "10.0.0.9:9000"}, headers=h)
    st = world.client.get(f"{API}/tally/sync", headers=h).json()
    assert st["latest"]["status"] == "FAILED" and "Could not reach Tally" in st["latest"]["error"]
    assert st["last_success"] is None and st["tally_url"] == ""  # a failed address is not saved


def test_one_run_at_a_time_and_permissions(world, monkeypatch):
    from datetime import UTC, datetime

    from app.modules.integrations.tally.models import TallySyncRun

    co, h = _setup(world, monkeypatch)
    world.db.add(TallySyncRun(company_id=co.id, tally_url="http://x:9000", status="RUNNING", started_at=datetime.now(UTC)))
    world.db.commit()
    r = world.client.post(f"{API}/tally/sync", json={"tally_url": "x"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "SYNC_RUNNING"
    sales = world.headers(world.user(co, ["SALES_EXECUTIVE"]), co)
    assert world.client.post(f"{API}/tally/sync", json={"tally_url": "x"}, headers=sales).status_code == 403


def test_one_sync_updates_everything(world, monkeypatch):
    """Dealers and balances, then godown stock, then delivered orders to Tally and their approvals — one button."""
    from app.modules.integrations.tally import sales

    co, h = _setup(world, monkeypatch)
    calls = []
    monkeypatch.setattr(sync, "_sync_stock", lambda db, run: (calls.append("stock"), ("OK", "42 godown-item lines"))[1])
    monkeypatch.setattr(sync, "_sales_push", lambda db, cid, actor: (calls.append("push"), ("OK", "2 sent"))[1])
    monkeypatch.setattr(sales, "check", lambda db, cid: calls.append("check") or
                        {"checked": 3, "approved": 2, "rejected": 0, "waiting": 1})

    world.client.post(f"{API}/tally/sync", json={"tally_url": "192.168.0.5"}, headers=h)
    st = world.client.get(f"{API}/tally/sync", headers=h).json()["latest"]
    assert st["status"] == "SUCCESS" and st["error"] is None, st
    steps = {x["key"]: (x["status"], x["detail"]) for x in st["summary"]["steps"]}
    assert steps == {"masters": ("OK", "1 customers, 1 godowns, 0 items"), "stock": ("OK", "42 godown-item lines"),
                     "sales_push": ("OK", "2 sent"), "sales_check": ("OK", "2 approved, 0 rejected, 1 still waiting")}
    assert calls == ["stock", "push", "check"]


def test_a_later_step_failing_keeps_the_rest(world, monkeypatch):
    from app.modules.integrations.tally import sales

    co, h = _setup(world, monkeypatch)
    monkeypatch.setattr(sync, "_sync_stock", lambda db, run: ("FAILED", "Tally did not answer"))

    def boom(db, cid):
        raise TallyError("Could not reach Tally")

    monkeypatch.setattr(sales, "check", boom)
    world.client.post(f"{API}/tally/sync", json={"tally_url": "192.168.0.5"}, headers=h)
    st = world.client.get(f"{API}/tally/sync", headers=h).json()["latest"]
    steps = {x["key"]: x["status"] for x in st["summary"]["steps"]}
    assert st["status"] == "SUCCESS" and steps == {"masters": "OK", "stock": "FAILED", "sales_push": "OK",
                                                   "sales_check": "FAILED"}
    assert "Godown stock" in st["error"] and "Approvals" in st["error"]
    world.db.expire_all()
    assert world.db.execute(select(Dealer).where(Dealer.tally_ledger_guid == "L1")).scalar_one().tally_balance == Decimal("185000.00")

    # Tally unreachable at the first step: the rest is skipped
    FakeTally.fail = "Could not reach Tally"
    world.client.post(f"{API}/tally/sync", json={}, headers=h)
    st = world.client.get(f"{API}/tally/sync", headers=h).json()["latest"]
    assert st["status"] == "FAILED" and [x["status"] for x in st["summary"]["steps"]] == ["FAILED", "SKIPPED", "SKIPPED", "SKIPPED"]
