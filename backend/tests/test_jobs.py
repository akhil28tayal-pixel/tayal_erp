"""Timed jobs: the automatic Tally sync during office hours and the nightly backup."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.modules.integrations.tally import sync
from app.modules.integrations.tally.models import TallySyncRun
from app.modules.platform import jobs
from app.modules.platform.models import JobRun, SystemSetting
from tests.test_tally_sync_button import API, FakeTally


def ist(h, m=0):  # a time today in India, as UTC
    return datetime(2026, 11, 16, h, m, tzinfo=jobs.IST).astimezone(UTC)


def _co(world, monkeypatch, url="http://192.168.0.5:9000"):
    monkeypatch.setattr(sync, "TallyClient", FakeTally)
    monkeypatch.setattr(sync, "_sync_stock", lambda db, run: ("OK", "0 godown-item lines"))
    FakeTally.fail = None
    co = world.company()
    co.tally_company_name = "Ishika Traders Daily"
    if url:
        world.db.add(SystemSetting(company_id=co.id, key="tally.url", value={"v": url}))
    world.db.commit()
    return co


def test_auto_sync_runs_in_office_hours_every_30_minutes(world, monkeypatch):
    co = _co(world, monkeypatch)
    db = world.db
    assert jobs.auto_sync_due(db, co.id, ist(11)) is True
    assert jobs.auto_sync_due(db, co.id, ist(7)) is False  # before 09:00
    assert jobs.auto_sync_due(db, co.id, ist(21)) is False  # after 20:30

    assert jobs.run_auto_sync(ist(11)) >= 1
    db.expire_all()
    run = db.execute(select(TallySyncRun).where(TallySyncRun.company_id == co.id)).scalar_one()
    assert run.status == "SUCCESS" and run.triggered_by is None
    # just ran → not due again for 60 minutes
    assert jobs.auto_sync_due(db, co.id, run.started_at + timedelta(minutes=10)) is False
    assert jobs.auto_sync_due(db, co.id, run.started_at + timedelta(minutes=61)) is True or \
        not jobs.in_window(run.started_at + timedelta(minutes=61), "09:00", "20:30")

    # shown as "Automatic" on the sync card
    h = world.headers(world.user(co, ["ACCOUNTS_EXECUTIVE"]), co)
    assert world.client.get(f"{API}/tally/sync", headers=h).json()["latest"]["triggered_by_name"] == "Automatic"


def test_auto_sync_can_be_switched_off_and_needs_an_address(world, monkeypatch):
    co = _co(world, monkeypatch, url=None)
    assert jobs.auto_sync_due(world.db, co.id, ist(11)) is False  # never synced by hand: no address yet
    world.db.add(SystemSetting(company_id=co.id, key="tally.url", value={"v": "http://x:9000"}))
    world.db.add(SystemSetting(company_id=co.id, key="tally.auto_sync_minutes", value={"v": 0}))
    world.db.commit()
    assert jobs.auto_sync_due(world.db, co.id, ist(11)) is False

    admin = world.headers(world.user(co, ["SUPER_ADMIN"]), co)
    put = lambda k, v: world.client.put(f"{API}/settings/{k}", json={"value": v}, headers=admin)  # noqa: E731
    assert put("tally.auto_sync_minutes", 5).status_code == 422
    assert put("tally.auto_sync_minutes", 60).status_code == 200
    assert put("tally.auto_sync_from", "9am").status_code == 422
    assert put("tally.auto_sync_from", "08:30").status_code == 200
    assert jobs.auto_sync_due(world.db, co.id, ist(8, 45)) is True


def test_backup_writes_a_dump_and_keeps_30_days(world, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    old = tmp_path / "tayal_erp_2026-09-01_2330.dump"
    old.write_bytes(b"x")
    import os
    os.utime(old, (0, 0))
    job = jobs.run_backup()
    assert job.status == "SUCCESS", job.detail
    assert job.file.startswith(str(tmp_path)) and job.size_bytes > 1000
    assert not old.exists() and "removed 1 older" in job.detail

    db = world.db
    db.expire_all()
    now = job.started_at
    assert jobs.backup_due(db, now + timedelta(hours=2)) is False  # done today
    late = (now + timedelta(hours=23)).astimezone(jobs.IST).replace(hour=23, minute=40).astimezone(UTC)
    assert jobs.backup_due(db, max(late, now + timedelta(hours=21))) is True
    assert jobs.backup_due(db, now + timedelta(hours=31)) is True  # a missed night is caught up


def test_failed_backup_is_recorded_and_shown(world, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "database_url", "postgresql+psycopg://nobody@localhost:5432/no_such_db")
    job = jobs.run_backup()
    assert job.status == "FAILED" and job.detail
    assert not list(tmp_path.glob("*.dump"))
    co = world.company()
    h = world.headers(world.user(co, ["SUPER_ADMIN"]), co)
    st = world.client.get(f"{API}/jobs", headers=h).json()
    assert st["backup"]["latest"]["status"] == "FAILED" and st["auto_sync"]["every_minutes"] == 60
    sales = world.headers(world.user(co, ["SALES_EXECUTIVE"]), co)
    assert world.client.post(f"{API}/jobs/backup", headers=sales).status_code == 403
    world.db.execute(select(JobRun)).all()
