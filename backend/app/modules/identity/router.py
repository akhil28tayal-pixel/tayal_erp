import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import audit_ctx, snapshot, write_audit
from app.core.db import get_db
from app.core.deps import Ctx, get_current_user, require
from app.core.errors import AppError, not_found
from app.core.pagination import Page, PageParams, paginate
from app.core.permissions import PERMISSIONS
from app.core.security import hash_password, verify_password
from app.modules.identity import service
from app.modules.identity.models import Role, RolePermission, User, UserGodownScope, UserRole
from app.modules.identity.schemas import (
    LoginIn,
    LoginOut,
    MeOut,
    PasswordChange,
    PasswordReset,
    RefreshIn,
    RoleOut,
    RolesAssign,
    TokenPair,
    UserCreate,
    UserListOut,
    UserOut,
    UserUpdate,
)
from app.modules.masters.models import Employee, Godown

auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(tags=["users"])


def _ip(request: Request) -> str | None:
    from app.core.deps import client_ip

    return client_ip(request)


COOKIE = "erp_rt"


def _set_cookie(response: Response, raw: str) -> None:
    from app.core.config import get_settings

    s = get_settings()
    response.set_cookie(COOKIE, raw, httponly=True, secure=s.env not in {"local", "test"}, samesite="strict",
                        path="/api/v1/auth", max_age=s.refresh_token_days * 86400)


def _cookie_mode(pair: dict, response: Response) -> dict:
    _set_cookie(response, pair["refresh_token"])
    return {**pair, "refresh_token": None}


@auth_router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user, pair = service.login(db, body.phone, body.password, body.device_id, _ip(request))
    if body.use_cookie:
        pair = _cookie_mode(pair, response)
    return {**pair, "user": user, "companies": service.company_access(db, user.id)}


@auth_router.post("/refresh", response_model=TokenPair)
def refresh(request: Request, response: Response, body: RefreshIn | None = None, db: Session = Depends(get_db)):
    raw = body.refresh_token if body and body.refresh_token else request.cookies.get(COOKIE)
    if not raw:
        raise AppError(401, "INVALID_REFRESH_TOKEN", "No refresh token")
    _, pair = service.refresh(db, raw, _ip(request))
    return _cookie_mode(pair, response) if not (body and body.refresh_token) else pair


@auth_router.post("/logout", status_code=204)
def logout(request: Request, response: Response, body: RefreshIn | None = None,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.core.security import hash_refresh_token
    from app.modules.identity.models import RefreshToken

    raw = body.refresh_token if body and body.refresh_token else request.cookies.get(COOKIE)
    response.delete_cookie(COOKIE, path="/api/v1/auth")
    if not raw:
        return
    token = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
    ).scalar_one_or_none()
    if token and token.user_id == user.id:
        service.revoke_family(db, token.family_id, "logout")
        db.commit()


@auth_router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"user": user, "companies": service.company_access(db, user.id)}


@auth_router.post("/password", status_code=204)
def change_password(body: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(422, "INVALID_CREDENTIALS", "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    service.revoke_all_sessions(db, user, "password_changed")
    write_audit(db, action="auth.password_changed", entity_type="users", entity_id=user.id,
                actor_id=user.id, company_id=None)
    db.commit()


# ------------------------------------------------------------------ users & roles (per company)
def _role_perms(db: Session, role_ids: list[uuid.UUID]) -> set[str]:
    return set(
        db.execute(select(RolePermission.permission_code).where(RolePermission.role_id.in_(role_ids))).scalars()
    )


def _apply_roles(db: Session, ctx: Ctx, user: User, role_codes: list[str], godown_ids: list[uuid.UUID] | None):
    roles = db.execute(select(Role).where(Role.code.in_(role_codes))).scalars().all()
    unknown = set(role_codes) - {r.code for r in roles}
    if unknown:
        raise AppError(422, "VALIDATION_ERROR", f"Unknown roles: {', '.join(sorted(unknown))}")
    # No privilege escalation: you can only grant what you hold yourself. "scope.*" entries restrict a user
    # (own godowns / own trips only) rather than grant power, so they are not compared.
    granted = {p for p in _role_perms(db, [r.id for r in roles]) if not p.startswith("scope.")}
    if not granted <= ctx.permissions:
        raise AppError(403, "FORBIDDEN", "You cannot grant permissions you do not hold")
    if user.id == ctx.user_id and "admin.users" not in granted:
        raise AppError(422, "SELF_LOCKOUT", "You cannot remove your own user-admin access")
    db.execute(delete(UserRole).where(UserRole.user_id == user.id, UserRole.company_id == ctx.company_id))
    for r in roles:
        db.add(UserRole(user_id=user.id, role_id=r.id, company_id=ctx.company_id))
    if godown_ids is not None:
        valid = set(
            db.execute(
                select(Godown.id).where(Godown.id.in_(godown_ids), Godown.company_id == ctx.company_id)
            ).scalars()
        )
        if valid != set(godown_ids):
            raise AppError(422, "VALIDATION_ERROR", "Unknown godown for this company")
        db.execute(delete(UserGodownScope).where(UserGodownScope.user_id == user.id))
        for gid in godown_ids:
            db.add(UserGodownScope(user_id=user.id, godown_id=gid))


def _guard_target(db: Session, ctx: Ctx, user: User) -> None:
    """You may only manage people who have no more power than you in this company: otherwise an admin could
    reset the owner's password (and sign in as the owner) or strip the owner's roles."""
    if user.id == ctx.user_id:
        return
    role_ids = list(db.execute(select(UserRole.role_id).where(UserRole.user_id == user.id,
                                                             UserRole.company_id == ctx.company_id)).scalars())
    theirs = {p for p in _role_perms(db, role_ids) if not p.startswith("scope.")} if role_ids else set()
    if not theirs <= ctx.permissions:
        raise AppError(403, "FORBIDDEN", "This person has more access than you. Ask the owner to make this change.")


def _user_in_company(db: Session, ctx: Ctx, user_id: uuid.UUID) -> User:
    user = db.execute(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .where(User.id == user_id, UserRole.company_id == ctx.company_id)
    ).scalars().first()
    if user is None:
        raise not_found("User")
    return user


def _with_roles(db: Session, ctx: Ctx, users: list[User]) -> list[dict]:
    ids = [u.id for u in users]
    roles: dict[uuid.UUID, list[str]] = {}
    for uid, code in db.execute(
        select(UserRole.user_id, Role.code).join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(ids), UserRole.company_id == ctx.company_id).order_by(Role.code)
    ):
        roles.setdefault(uid, []).append(code)
    godowns: dict[uuid.UUID, list[uuid.UUID]] = {}
    for uid, gid in db.execute(
        select(UserGodownScope.user_id, UserGodownScope.godown_id).where(UserGodownScope.user_id.in_(ids))
    ):
        godowns.setdefault(uid, []).append(gid)
    return [{**UserOut.model_validate(u).model_dump(), "roles": roles.get(u.id, []),
             "godown_ids": godowns.get(u.id, [])} for u in users]


@users_router.get("/users", response_model=Page[UserListOut])
def list_users(params: PageParams = Depends(), q: str | None = None,
               ctx: Ctx = Depends(require("admin.users")), db: Session = Depends(get_db)):
    """Everyone who has, or had, a role in this company (users whose roles were all removed stay listed)."""
    stmt = select(User).where(
        User.id.in_(select(UserRole.user_id).where(UserRole.company_id == ctx.company_id))
        | User.employee_id.in_(select(Employee.id).where(Employee.company_id == ctx.company_id))
    )
    if q:
        stmt = stmt.where(User.full_name.ilike(f"%{q}%") | User.phone.ilike(f"%{q}%"))
    page = paginate(db, stmt, params, {"full_name": User.full_name, "phone": User.phone}, User.full_name)
    return {**page, "items": _with_roles(db, ctx, page["items"])}


@users_router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, ctx: Ctx = Depends(require("admin.users")), db: Session = Depends(get_db)):
    if body.employee_id and db.get(Employee, body.employee_id) is None:
        raise AppError(422, "VALIDATION_ERROR", "Unknown employee")
    user = User(phone=body.phone, email=body.email, full_name=body.full_name,
                password_hash=hash_password(body.password), employee_id=body.employee_id,
                created_by=ctx.user_id)
    db.add(user)
    db.flush()
    _apply_roles(db, ctx, user, body.roles, body.godown_ids)
    service.ensure_employee(db, user, ctx.company_id, ctx.user_id)
    audit_ctx(db, ctx, "user.create", user)
    db.commit()
    return user


@users_router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, ctx: Ctx = Depends(require("admin.users")), db: Session = Depends(get_db)):
    return _user_in_company(db, ctx, user_id)


@users_router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, ctx: Ctx = Depends(require("admin.users")),
                db: Session = Depends(get_db)):
    user = _user_in_company(db, ctx, user_id)
    _guard_target(db, ctx, user)
    if body.employee_id is not None:
        emp = db.get(Employee, body.employee_id)
        if emp is None or emp.company_id != ctx.company_id:
            raise AppError(422, "VALIDATION_ERROR", "Unknown employee")
    before = snapshot(user)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    user.updated_by = ctx.user_id
    audit_ctx(db, ctx, "user.update", user, before)
    db.commit()
    return user


@users_router.put("/users/{user_id}/roles", response_model=UserListOut)
def assign_roles(user_id: uuid.UUID, body: RolesAssign, ctx: Ctx = Depends(require("admin.users")),
                 db: Session = Depends(get_db)):
    """Replace the user's roles in this company (and godowns, for godown supervisors)."""
    user = db.get(User, user_id)
    if user is None:
        raise not_found("User")
    _guard_target(db, ctx, user)
    _apply_roles(db, ctx, user, body.roles, body.godown_ids)
    service.ensure_employee(db, user, ctx.company_id, ctx.user_id)
    write_audit(db, action="user.roles_assigned", entity_type="users", entity_id=user.id, actor_id=ctx.user_id,
                company_id=ctx.company_id, after={"roles": body.roles,
                                                  "godown_ids": [str(g) for g in body.godown_ids or []]})
    db.commit()
    return _with_roles(db, ctx, [user])[0]


@users_router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: uuid.UUID, ctx: Ctx = Depends(require("admin.users")),
                    db: Session = Depends(get_db)):
    user = _user_in_company(db, ctx, user_id)
    if user.id == ctx.user_id:
        raise AppError(422, "VALIDATION_ERROR", "You cannot deactivate yourself")
    _guard_target(db, ctx, user)
    before = snapshot(user)
    user.is_active = False
    service.revoke_all_sessions(db, user, "deactivated")
    audit_ctx(db, ctx, "user.deactivate", user, before)
    db.commit()
    return user


@users_router.post("/users/{user_id}/activate", response_model=UserOut)
def activate_user(user_id: uuid.UUID, ctx: Ctx = Depends(require("admin.users")), db: Session = Depends(get_db)):
    """Let a deactivated person sign in again (with their old password, or reset it first)."""
    user = _user_in_company(db, ctx, user_id)
    _guard_target(db, ctx, user)
    before = snapshot(user)
    user.is_active = True
    user.failed_logins = 0
    user.locked_until = None
    audit_ctx(db, ctx, "user.activate", user, before)
    db.commit()
    return user


@users_router.post("/users/{user_id}/password", status_code=204)
def reset_password(user_id: uuid.UUID, body: PasswordReset, ctx: Ctx = Depends(require("admin.users")),
                   db: Session = Depends(get_db)):
    """Set a new password for someone who forgot theirs. Signs them out on every device; unlocks the account."""
    user = _user_in_company(db, ctx, user_id)
    _guard_target(db, ctx, user)
    user.password_hash = hash_password(body.new_password)
    user.failed_logins = 0
    user.locked_until = None
    service.revoke_all_sessions(db, user, "password_reset")
    write_audit(db, action="user.password_reset", entity_type="users", entity_id=user.id, actor_id=ctx.user_id,
                company_id=ctx.company_id)
    db.commit()


@users_router.get("/roles", response_model=list[RoleOut])
def list_roles(ctx: Ctx = Depends(require("admin.users")), db: Session = Depends(get_db)):
    roles = db.execute(select(Role).order_by(Role.code)).scalars().all()
    return [
        RoleOut(id=r.id, code=r.code, name=r.name, is_system=r.is_system,
                permissions=sorted(_role_perms(db, [r.id])))
        for r in roles
    ]


@users_router.get("/permissions")
def list_permissions(ctx: Ctx = Depends(require("admin.users"))):
    return [{"code": k, "description": v} for k, v in PERMISSIONS.items()]
