import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.deps import Ctx, require
from app.core.errors import AppError
from app.modules.platform.models import AuditLog, SystemSetting
from app.modules.platform.settings import DEFAULT_SETTINGS, get_setting

router = APIRouter()
health_router = APIRouter(tags=["health"])


@health_router.get("/health/live")
def live():
    return {"status": "ok"}


@health_router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    checks: dict[str, str] = {"database": "ok"}
    try:
        import redis

        from app.core.config import get_settings

        redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=1).ping()
        checks["redis"] = "ok"
    except Exception:  # noqa: BLE001 - readiness reports, never raises
        checks["redis"] = "unavailable"
    return {"status": "ok" if all(v == "ok" for v in checks.values()) else "degraded", "checks": checks}


class SettingIn(BaseModel):
    value: Any


@router.get("/settings", tags=["settings"])
def list_settings(ctx: Ctx = Depends(require("masters.view")), db: Session = Depends(get_db)):
    return {key: get_setting(db, ctx.company_id, key) for key in DEFAULT_SETTINGS}


@router.put("/settings/{key}", tags=["settings"])
def put_setting(key: str, body: SettingIn, ctx: Ctx = Depends(require("admin.settings")),
                db: Session = Depends(get_db)):
    if key not in DEFAULT_SETTINGS:
        raise AppError(422, "UNKNOWN_SETTING", f"Unknown setting '{key}'")
    expected = type(DEFAULT_SETTINGS[key])
    if not isinstance(body.value, expected) or (expected is int and isinstance(body.value, bool)):
        raise AppError(422, "VALIDATION_ERROR", f"'{key}' expects {expected.__name__}")
    if key in ("tally.auto_sync_from", "tally.auto_sync_to"):
        import re

        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", body.value):
            raise AppError(422, "VALIDATION_ERROR", "Use a time like 09:00 or 20:30")
    if key == "tally.auto_sync_minutes" and body.value != 0 and not 10 <= body.value <= 720:
        raise AppError(422, "VALIDATION_ERROR", "Every 10 to 720 minutes, or 0 to switch it off")
    if key == "whatsapp.balance_template" and "{balance}" not in body.value:
        raise AppError(422, "VALIDATION_ERROR", "The message must contain {balance}")
    if key == "whatsapp.template_params":
        fields = [f.strip() for f in str(body.value).split(",") if f.strip()]
        if not fields or any(f not in ("dealer", "balance", "as_of", "sender") for f in fields):
            raise AppError(422, "VALIDATION_ERROR", "List the template's {{1}}, {{2}}, ... as: dealer, balance, as_of, sender")
    if key == "whatsapp.provider" and body.value not in ("CLICK_TO_CHAT", "CLOUD_API"):
        raise AppError(422, "VALIDATION_ERROR", "Use CLICK_TO_CHAT or CLOUD_API")
    if key == "whatsapp.provider" and body.value == "CLOUD_API":
        from app.modules.collections.whatsapp_cloud import configured

        if not configured():
            raise AppError(422, "VALIDATION_ERROR", "Add the WhatsApp API keys to the .env file and restart first")
    row = db.execute(
        select(SystemSetting).where(SystemSetting.company_id == ctx.company_id, SystemSetting.key == key)
    ).scalar_one_or_none()
    before = get_setting(db, ctx.company_id, key)
    if row is None:
        row = SystemSetting(company_id=ctx.company_id, key=key, value={"v": body.value})
        db.add(row)
    else:
        row.value = {"v": body.value}
    row.updated_by = ctx.user_id
    write_audit(db, action="setting.update", entity_type="system_settings", entity_id=key, actor_id=ctx.user_id,
                company_id=ctx.company_id, before={"value": before}, after={"value": body.value})
    db.commit()
    return {key: body.value}


@router.get("/audit", tags=["audit"])
def audit_log(entity_type: str | None = None, entity_id: str | None = None, actor_id: uuid.UUID | None = None,
              date_from: datetime | None = None, date_to: datetime | None = None,
              limit: int = Query(100, le=500), before_id: int | None = None,
              ctx: Ctx = Depends(require("admin.audit_view")), db: Session = Depends(get_db)):
    stmt = select(AuditLog).where(AuditLog.company_id == ctx.company_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if date_from:
        stmt = stmt.where(AuditLog.at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.at < date_to)
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)
    rows = db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit)).scalars().all()
    return {
        "items": [
            {"id": r.id, "at": r.at, "actor_id": r.actor_id, "action": r.action, "entity_type": r.entity_type,
             "entity_id": r.entity_id, "before": r.before, "after": r.after}
            for r in rows
        ],
        "next_cursor": rows[-1].id if len(rows) == limit else None,
    }


# ---------------------------------------------------------------- timed jobs: automatic Tally sync, nightly backup
def _job(j) -> dict | None:
    if j is None:
        return None
    return {"id": j.id, "status": j.status, "started_at": j.started_at, "finished_at": j.finished_at,
            "detail": j.detail, "file": j.file, "size_bytes": j.size_bytes}


@router.get("/jobs", tags=["settings"])
def jobs_status(ctx: Ctx = Depends(require("tally.view")), db: Session = Depends(get_db)):
    """What runs by itself: the automatic Tally sync and the nightly backup."""
    from app.core.config import get_settings
    from app.modules.platform import jobs
    from app.modules.platform.models import JobRun

    s = get_settings()
    latest = db.execute(select(JobRun).where(JobRun.job == "BACKUP").order_by(JobRun.started_at.desc()).limit(1)
                        ).scalar_one_or_none()
    return {
        "scheduler_on": s.scheduler_enabled and s.env != "test",
        "auto_sync": {"every_minutes": get_setting(db, ctx.company_id, "tally.auto_sync_minutes"),
                      "from": get_setting(db, ctx.company_id, "tally.auto_sync_from"),
                      "to": get_setting(db, ctx.company_id, "tally.auto_sync_to")},
        "backup": {"folder": str(jobs.backup_dir()), "time": s.backup_time, "keep_days": s.backup_keep_days,
                   "latest": _job(latest), "last_success": _job(jobs.last_backup(db))},
    }


@router.post("/jobs/backup", tags=["settings"], status_code=202)
def backup_now(background: BackgroundTasks, ctx: Ctx = Depends(require("admin.settings")),
               db: Session = Depends(get_db)):
    from app.modules.platform import jobs

    write_audit(db, action="backup.manual", entity_type="job_runs", entity_id=None, actor_id=ctx.user_id,
                company_id=ctx.company_id)
    db.commit()
    background.add_task(jobs.run_backup)
    return {"started": True}


# ---------------------------------------------------------------- notifications (web bell, phone notifications)
class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str | None
    link: str | None
    created_at: datetime
    read_at: datetime | None


class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]


class ReadIn(BaseModel):
    ids: list[uuid.UUID] | None = None  # None = mark all read


@router.get("/notifications", response_model=NotificationsOut, tags=["notifications"])
def my_notifications(after: datetime | None = None, limit: int = Query(30, ge=1, le=100),
                     ctx: Ctx = Depends(require()), db: Session = Depends(get_db)):
    """The signed-in user's notifications, newest first. `after` returns only newer ones (the phone polls this)."""
    from sqlalchemy import func as sa_func

    from app.modules.platform.models import Notification

    base = select(Notification).where(Notification.user_id == ctx.user_id, Notification.company_id == ctx.company_id)
    q = base.where(Notification.created_at > after) if after else base
    items = db.execute(q.order_by(Notification.created_at.desc()).limit(limit)).scalars().all()
    unread = db.execute(select(sa_func.count()).select_from(
        base.where(Notification.read_at.is_(None)).subquery())).scalar_one()
    return {"unread": unread, "items": items}


@router.post("/notifications/read", tags=["notifications"])
def mark_read(body: ReadIn, ctx: Ctx = Depends(require()), db: Session = Depends(get_db)):
    from sqlalchemy import func as sa_func
    from sqlalchemy import update

    from app.modules.platform.models import Notification

    stmt = update(Notification).where(Notification.user_id == ctx.user_id, Notification.company_id == ctx.company_id,
                                      Notification.read_at.is_(None))
    if body.ids:
        stmt = stmt.where(Notification.id.in_(body.ids))
    n = db.execute(stmt.values(read_at=sa_func.now())).rowcount
    db.commit()
    return {"marked": n}
