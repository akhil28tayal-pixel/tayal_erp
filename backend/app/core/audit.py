import uuid
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.modules.platform.models import AuditLog

_SKIP = {"password_hash", "created_at", "updated_at", "created_by", "updated_by"}


def snapshot(obj: Any) -> dict:
    """Column values of an ORM object, JSON-safe, without secrets/noise."""
    mapper = inspect(obj).mapper
    # Decimals stay exact strings — money and quantities must never pass through float.
    return jsonable_encoder(
        {c.key: getattr(obj, c.key) for c in mapper.column_attrs if c.key not in _SKIP},
        custom_encoder={Decimal: str},
    )


def diff(before: dict | None, after: dict | None) -> tuple[dict | None, dict | None]:
    if before is None or after is None:
        return before, after
    keys = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


def write_audit(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str | None,
    actor_id: uuid.UUID | None,
    company_id: uuid.UUID | None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    b, a = diff(before, after)
    db.add(
        AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            actor_id=actor_id,
            company_id=company_id,
            before=b,
            after=a,
            ip=ip,
            request_id=request_id,
        )
    )


def audit_ctx(db: Session, ctx, action: str, obj: Any, before: dict | None = None) -> None:
    """Shortcut for request handlers: audit an entity change using the request context."""
    write_audit(
        db,
        action=action,
        entity_type=obj.__tablename__,
        entity_id=obj.id,
        actor_id=ctx.user_id,
        company_id=ctx.company_id,
        before=before,
        after=snapshot(obj),
        ip=ctx.ip,
        request_id=ctx.request_id,
    )
