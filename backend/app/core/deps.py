"""Request context: authenticated user + company scope + permissions. Every business endpoint depends on
`require(...)`, which is the backend authorization gate (menus in clients are cosmetic only)."""

import uuid
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, forbidden
from app.core.security import decode_access_token
from app.modules.identity.models import RolePermission, User, UserGodownScope, UserRole
from app.modules.masters.models import Company

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None:
        raise AppError(401, "UNAUTHENTICATED", "Missing bearer token")
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise AppError(401, "TOKEN_EXPIRED", "Access token expired") from None
    except jwt.InvalidTokenError:
        raise AppError(401, "UNAUTHENTICATED", "Invalid access token") from None
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active or user.token_version != payload.get("tv"):
        raise AppError(401, "UNAUTHENTICATED", "Session is no longer valid")
    return user


@dataclass
class Ctx:
    user: User
    company_id: uuid.UUID
    permissions: set[str]
    godown_ids: set[uuid.UUID] | None = None  # None = not godown-restricted
    request_id: str | None = None
    ip: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def has(self, perm: str) -> bool:
        return perm in self.permissions


def load_permissions(db: Session, user_id: uuid.UUID, company_id: uuid.UUID) -> set[str]:
    rows = db.execute(
        select(RolePermission.permission_code)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id, UserRole.company_id == company_id)
    ).scalars()
    return set(rows)


def get_ctx(
    request: Request,
    x_company_id: uuid.UUID = Header(..., alias="X-Company-Id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Ctx:
    company = db.get(Company, x_company_id)
    if company is None or not company.is_active:
        raise forbidden("You have no access to this company", code="SCOPE_FORBIDDEN")
    perms = load_permissions(db, user.id, x_company_id)
    if not perms:
        raise forbidden("You have no access to this company", code="SCOPE_FORBIDDEN")
    godowns = None
    if "scope.godown_restricted" in perms:
        godowns = set(
            db.execute(select(UserGodownScope.godown_id).where(UserGodownScope.user_id == user.id)).scalars()
        )
    return Ctx(
        user=user,
        company_id=x_company_id,
        permissions=perms,
        godown_ids=godowns,
        request_id=getattr(request.state, "request_id", None),
        ip=client_ip(request),
    )


def require(*perms: str):
    """Dependency factory: all listed permissions are required in the current company."""

    def _dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        missing = [p for p in perms if p not in ctx.permissions]
        if missing:
            raise forbidden(f"Missing permission: {', '.join(missing)}")
        return ctx

    return _dep


def client_ip(request: Request) -> str | None:
    """The visitor's real address. Behind Cloudflare Tunnel / the web server on the same PC every request comes
    from 127.0.0.1, so the address Cloudflare (or the local proxy) passes on is used — only in that case, so it
    cannot be faked from outside."""
    direct = request.client.host if request.client else None
    if direct in ("127.0.0.1", "::1", "localhost"):
        cf = request.headers.get("cf-connecting-ip")
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        return (cf or fwd or direct)[:64]
    return direct
