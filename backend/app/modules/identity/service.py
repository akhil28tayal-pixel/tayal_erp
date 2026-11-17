import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.db import uuid7
from app.core.errors import AppError
from app.core.security import (
    burn_password_check,
    create_access_token,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.modules.identity.models import RefreshToken, Role, RolePermission, User, UserRole
from app.modules.masters.models import Company

INVALID_LOGIN = AppError(401, "INVALID_CREDENTIALS", "Phone number or password is incorrect")


def _issue_pair(db: Session, user: User, family_id: uuid.UUID, device_id: str | None) -> dict:
    s = get_settings()
    raw, token_hash = new_refresh_token()
    now = datetime.now(UTC)
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=token_hash,
            device_id=device_id,
            issued_at=now,
            expires_at=now + timedelta(days=s.refresh_token_days),
        )
    )
    return {
        "access_token": create_access_token(user.id, family_id, user.token_version),
        "refresh_token": raw,
        "expires_in": s.access_token_minutes * 60,
    }


def login(db: Session, phone: str, password: str, device_id: str | None, ip: str | None) -> tuple[User, dict]:
    s = get_settings()
    user = db.execute(select(User).where(User.phone == phone).with_for_update()).scalar_one_or_none()
    now = datetime.now(UTC)
    if user is None:
        burn_password_check(password)
        raise INVALID_LOGIN
    if user.locked_until and user.locked_until > now:
        raise AppError(423, "ACCOUNT_LOCKED", "Too many failed attempts. Try again later.")
    if not user.is_active:
        raise INVALID_LOGIN
    if not verify_password(password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= s.login_max_failures:
            user.locked_until = now + timedelta(minutes=s.login_lock_minutes)
            user.failed_logins = 0
        write_audit(db, action="auth.login_failed", entity_type="users", entity_id=user.id,
                    actor_id=user.id, company_id=None, ip=ip)
        db.commit()
        raise INVALID_LOGIN
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    pair = _issue_pair(db, user, uuid7(), device_id)
    write_audit(db, action="auth.login", entity_type="users", entity_id=user.id, actor_id=user.id,
                company_id=None, ip=ip)
    db.commit()
    return user, pair


def refresh(db: Session, raw_token: str, ip: str | None) -> tuple[User, dict]:
    """Rotate the refresh token. Presenting an already-rotated/revoked token is treated as theft:
    the whole token family (session) is revoked."""
    now = datetime.now(UTC)
    token = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token)).with_for_update()
    ).scalar_one_or_none()
    if token is None:
        raise AppError(401, "INVALID_REFRESH_TOKEN", "Refresh token is not valid")
    if token.rotated_at is not None or token.revoked_at is not None:
        revoke_family(db, token.family_id, "reuse_detected")
        write_audit(db, action="auth.refresh_reuse_detected", entity_type="users", entity_id=token.user_id,
                    actor_id=token.user_id, company_id=None, ip=ip)
        db.commit()
        raise AppError(401, "REFRESH_TOKEN_REUSED", "Session revoked. Please log in again.")
    if token.expires_at <= now:
        raise AppError(401, "INVALID_REFRESH_TOKEN", "Refresh token expired")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise AppError(401, "INVALID_REFRESH_TOKEN", "User is not active")
    token.rotated_at = now
    pair = _issue_pair(db, user, token.family_id, token.device_id)
    db.commit()
    return user, pair


def revoke_family(db: Session, family_id: uuid.UUID, reason: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoke_reason=reason)
    )


def revoke_all_sessions(db: Session, user: User, reason: str) -> None:
    user.token_version += 1  # invalidates outstanding access tokens immediately
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoke_reason=reason)
    )


def company_access(db: Session, user_id: uuid.UUID) -> list[dict]:
    rows = db.execute(
        select(Company, Role)
        .join(UserRole, UserRole.company_id == Company.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == user_id, Company.is_active.is_(True))
        .order_by(Company.code)
    ).all()
    out: dict[uuid.UUID, dict] = {}
    role_ids: dict[uuid.UUID, list[uuid.UUID]] = {}
    for company, role in rows:
        entry = out.setdefault(
            company.id,
            {"company_id": company.id, "company_code": company.code,
             "company_name": company.trade_name or company.legal_name, "roles": [], "permissions": []},
        )
        entry["roles"].append(role.code)
        role_ids.setdefault(company.id, []).append(role.id)
    for cid, rids in role_ids.items():
        perms = db.execute(
            select(RolePermission.permission_code).where(RolePermission.role_id.in_(rids)).distinct()
        ).scalars()
        out[cid]["permissions"] = sorted(perms)
    return list(out.values())


def ensure_employee(db: Session, user: User, company_id: uuid.UUID, actor_id: uuid.UUID | None) -> None:
    """Every user who works in a company is also an employee there, so they can be assigned dealers,
    godowns and (later) targets. Creates the employee record if the user has none."""
    from app.modules.masters.models import Employee

    if user.employee_id is not None:
        return
    code = f"E-{user.phone}"
    emp = db.execute(
        select(Employee).where(Employee.company_id == company_id, Employee.code == code)
    ).scalar_one_or_none()
    if emp is None:
        emp = Employee(company_id=company_id, code=code, full_name=user.full_name, phone=user.phone,
                       created_by=actor_id)
        db.add(emp)
        db.flush()
        write_audit(db, action="employee.created_for_user", entity_type="employees", entity_id=emp.id,
                    actor_id=actor_id, company_id=company_id, after={"user_id": str(user.id), "name": user.full_name})
    user.employee_id = emp.id
    db.flush()


def backfill_employees(db: Session, company_id: uuid.UUID) -> int:
    """Create employee records for users of this company that have none (users added before this rule)."""
    users = db.execute(
        select(User).join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.company_id == company_id, User.employee_id.is_(None)).distinct()
    ).scalars().all()
    for u in users:
        ensure_employee(db, u, company_id, None)
    return len(users)
