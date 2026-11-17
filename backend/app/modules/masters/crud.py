"""Small CRUD builder for simple company-scoped masters, so every one gets the same permission checks,
company isolation, optimistic error handling, pagination and audit trail."""

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.audit import audit_ctx, snapshot
from app.core.db import get_db
from app.core.deps import Ctx, require
from app.core.errors import not_found
from app.core.pagination import Page, PageParams, paginate


def get_scoped(db: Session, model, obj_id: uuid.UUID, ctx: Ctx, entity: str):
    """Fetch by id within the caller's company. A row of another company is reported as 404, not 403,
    so ids cannot be probed across companies."""
    obj = db.get(model, obj_id)
    if obj is None or (hasattr(model, "company_id") and obj.company_id != ctx.company_id):
        raise not_found(entity)
    return obj


def register_crud(
    router: APIRouter,
    *,
    path: str,
    model,
    entity: str,
    create_schema: type[BaseModel],
    update_schema: type[BaseModel],
    out_schema: type[BaseModel],
    view_perm: str,
    manage_perm: str,
    sortable: dict,
    default_sort,
    search_cols: tuple = (),
    company_scoped: bool = True,
    scope_filter: Callable[[Select, Ctx], Select] | None = None,
    validate: Callable[[Session, Ctx, dict], None] | None = None,
    allow_create: bool = True,
    allow_list: bool = True,
) -> None:
    tag = [path.strip("/").split("/")[0]]

    def _list(params: PageParams = Depends(), q: str | None = None, active: bool | None = None,
              ctx: Ctx = Depends(require(view_perm)), db: Session = Depends(get_db)):
        stmt = select(model)
        if company_scoped:
            stmt = stmt.where(model.company_id == ctx.company_id)
        if q and search_cols:
            from sqlalchemy import or_

            stmt = stmt.where(or_(*[c.ilike(f"%{q}%") for c in search_cols]))
        if active is not None and hasattr(model, "is_active"):
            stmt = stmt.where(model.is_active.is_(active))
        if scope_filter:
            stmt = scope_filter(stmt, ctx)
        return paginate(db, stmt, params, sortable, default_sort)

    if allow_list:
        router.get(path, response_model=Page[out_schema], tags=tag, name=f"list_{entity}")(_list)

    def _create(body: create_schema, ctx: Ctx = Depends(require(manage_perm)),  # type: ignore[valid-type]
                db: Session = Depends(get_db)):
        data = body.model_dump()
        if validate:
            validate(db, ctx, data)
        extra = {"company_id": ctx.company_id} if company_scoped else {}
        obj = model(**data, **extra, created_by=ctx.user_id)
        db.add(obj)
        db.flush()
        db.refresh(obj)  # audit what the database stored (e.g. NUMERIC scale), not the raw input
        audit_ctx(db, ctx, f"{entity}.create", obj)
        db.commit()
        return obj

    if allow_create:
        router.post(path, response_model=out_schema, status_code=201, tags=tag, name=f"create_{entity}")(_create)

    @router.get(path + "/{obj_id}", response_model=out_schema, tags=tag, name=f"get_{entity}")
    def _get(obj_id: uuid.UUID, ctx: Ctx = Depends(require(view_perm)), db: Session = Depends(get_db)):
        obj = get_scoped(db, model, obj_id, ctx, entity)
        if scope_filter and db.execute(scope_filter(select(model).where(model.id == obj_id), ctx)).first() is None:
            raise not_found(entity)
        return obj

    @router.patch(path + "/{obj_id}", response_model=out_schema, tags=tag, name=f"update_{entity}")
    def _update(obj_id: uuid.UUID, body: update_schema,  # type: ignore[valid-type]
                ctx: Ctx = Depends(require(manage_perm)), db: Session = Depends(get_db)):
        obj = get_scoped(db, model, obj_id, ctx, entity)
        data = body.model_dump(exclude_unset=True)
        if validate:
            validate(db, ctx, data)
        before = snapshot(obj)
        for k, v in data.items():
            setattr(obj, k, v)
        obj.updated_by = ctx.user_id
        db.flush()
        db.refresh(obj)
        audit_ctx(db, ctx, f"{entity}.update", obj, before)
        db.commit()
        return obj
