"""Timed jobs that run inside the API process: automatic Tally sync during office hours and a nightly database
backup. One background thread checks every minute. A Postgres advisory lock makes sure only one process runs them,
even if the server is ever started with several workers."""

import os
import subprocess
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, get_engine
from app.modules.platform.models import JobRun
from app.modules.platform.settings import get_setting

log = structlog.get_logger()
LOCK_ID = 7_231_001
IST = ZoneInfo("Asia/Kolkata")


def _hhmm(v: str, default: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(v or default).split(":"))
        return h, m
    except ValueError:
        h, m = default.split(":")
        return int(h), int(m)


def in_window(now: datetime, start: str, end: str) -> bool:
    t = now.astimezone(IST)
    a, b = _hhmm(start, "09:00"), _hhmm(end, "20:30")
    return a <= (t.hour, t.minute) <= b


# ------------------------------------------------------------------ automatic Tally sync
def auto_sync_due(db: Session, company_id, now: datetime) -> bool:
    from app.modules.integrations.tally import sync
    from app.modules.integrations.tally.models import TallySyncRun

    every = int(get_setting(db, company_id, "tally.auto_sync_minutes") or 0)
    if every <= 0 or not get_setting(db, company_id, "tally.url"):
        return False
    if not in_window(now, get_setting(db, company_id, "tally.auto_sync_from"),
                     get_setting(db, company_id, "tally.auto_sync_to")):
        return False
    if sync.running_run(db, company_id):
        return False
    last = db.execute(select(TallySyncRun.started_at).where(TallySyncRun.company_id == company_id)
                      .order_by(TallySyncRun.started_at.desc()).limit(1)).scalar_one_or_none()
    return last is None or now - last >= timedelta(minutes=every)


def run_auto_sync(now: datetime | None = None) -> int:
    """Start (and finish) a sync for every company that is due. Returns how many ran."""
    from app.modules.integrations.tally import sync
    from app.modules.integrations.tally.models import TallySyncRun
    from app.modules.masters.models import Company

    now = now or datetime.now(UTC)
    ran = 0
    db = SessionLocal()
    try:
        for company in db.execute(select(Company).where(Company.is_active.is_(True))).scalars().all():
            if not auto_sync_due(db, company.id, now):
                continue
            run = TallySyncRun(company_id=company.id, tally_url=sync.normalize_url(get_setting(db, company.id, "tally.url")),
                               status="RUNNING", started_at=datetime.now(UTC), triggered_by=None)
            db.add(run)
            db.commit()
            sync.run_in_background(run.id)
            ran += 1
    finally:
        db.close()
    return ran


# ------------------------------------------------------------------ nightly backup
def backup_dir() -> Path:
    return Path(os.path.expanduser(get_settings().backup_dir))


def last_backup(db: Session) -> JobRun | None:
    return db.execute(select(JobRun).where(JobRun.job == "BACKUP", JobRun.status == "SUCCESS")
                      .order_by(JobRun.started_at.desc()).limit(1)).scalar_one_or_none()


def backup_due(db: Session, now: datetime) -> bool:
    if db.execute(select(JobRun.id).where(JobRun.job == "BACKUP", JobRun.status == "RUNNING",
                                          JobRun.started_at > now - timedelta(hours=2))).first():
        return False
    last = last_backup(db)
    if last and now - last.started_at < timedelta(hours=20):
        return False
    if last is None or now - last.started_at > timedelta(hours=30):
        return True  # never done, or a night was missed (Mac asleep): catch up now
    t = now.astimezone(IST)
    return (t.hour, t.minute) >= _hhmm(get_settings().backup_time, "23:30")


def run_backup() -> JobRun:
    """pg_dump the whole database (custom format, restore with pg_restore) and keep the last N days."""
    settings = get_settings()
    db = SessionLocal()
    job = JobRun(job="BACKUP", status="RUNNING", started_at=datetime.now(UTC))
    db.add(job)
    db.commit()
    try:
        url = make_url(settings.database_url)
        folder = backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(IST).strftime("%Y-%m-%d_%H%M")
        out = folder / f"tayal_erp_{stamp}.dump"
        env = {**os.environ}
        if url.password:
            env["PGPASSWORD"] = url.password
        pg_dump = str(Path(settings.pg_bin_dir) / "pg_dump") if settings.pg_bin_dir else "pg_dump"
        cmd = [pg_dump, "-Fc", "-h", url.host or "localhost", "-p", str(url.port or 5432), "-f", str(out)]
        if url.username:
            cmd += ["-U", url.username]
        cmd.append(url.database)
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800, check=False)
        if r.returncode != 0:
            out.unlink(missing_ok=True)
            raise RuntimeError((r.stderr or "pg_dump failed").strip()[:500])
        removed = _prune(folder, settings.backup_keep_days)
        job.status, job.file, job.size_bytes = "SUCCESS", str(out), out.stat().st_size
        job.detail = f"Kept the last {settings.backup_keep_days} days" + (f", removed {removed} older" if removed else "")
    except Exception as e:  # noqa: BLE001 - recorded and shown on the Settings page
        job.status, job.detail = "FAILED", str(e) if not isinstance(e, FileNotFoundError) else \
            "pg_dump was not found. Start the server with ./start-mac.sh so PostgreSQL's tools are on the PATH."
    job.finished_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)
    db.expunge(job)
    db.close()
    log.info("backup", status=job.status, file=job.file, detail=job.detail)
    return job


def _prune(folder: Path, keep_days: int) -> int:
    cutoff = time.time() - keep_days * 86400
    n = 0
    for f in folder.glob("tayal_erp_*.dump"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            n += 1
    return n


# ------------------------------------------------------------------ the loop
def tick(now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    for name, fn in (("auto_sync", lambda: run_auto_sync(now)), ("backup", _backup_if_due)):
        try:
            fn()
        except Exception:  # noqa: BLE001 - one job failing must not stop the loop
            log.error("job_failed", job=name)
            traceback.print_exc()


def _backup_if_due() -> None:
    db = SessionLocal()
    try:
        due = backup_due(db, datetime.now(UTC))
    finally:
        db.close()
    if due:
        run_backup()


def _loop(stop: threading.Event) -> None:
    conn = None
    while not stop.is_set():
        try:
            if conn is None:
                c = get_engine().connect()
                if c.execute(text("select pg_try_advisory_lock(:k)"), {"k": LOCK_ID}).scalar():
                    conn = c  # held for as long as this process lives
                    log.info("scheduler_started")
                else:
                    c.close()
            if conn is not None:
                tick()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            conn = None
        stop.wait(60)


_stop = threading.Event()


def start() -> None:
    if not get_settings().scheduler_enabled or get_settings().env == "test":
        return
    threading.Thread(target=_loop, args=(_stop,), name="erp-jobs", daemon=True).start()


def stop() -> None:
    _stop.set()
