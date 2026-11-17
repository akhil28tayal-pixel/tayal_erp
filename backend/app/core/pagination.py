from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int


class PageParams:
    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        sort: str | None = Query(None, description="Comma separated, '-' prefix for descending"),
    ):
        self.page, self.page_size, self.sort = page, page_size, sort


def paginate(db: Session, stmt: Select, params: PageParams, sortable: dict, default_sort) -> dict:
    """`sortable` maps public field names to columns; anything else is rejected (no arbitrary sorting)."""
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    order = []
    if params.sort:
        for part in params.sort.split(","):
            desc = part.startswith("-")
            name = part.lstrip("-")
            if name not in sortable:
                raise AppError(422, "VALIDATION_ERROR", f"Cannot sort by '{name}'")
            col = sortable[name]
            order.append(col.desc() if desc else col.asc())
    else:
        order = default_sort if isinstance(default_sort, list) else [default_sort]
    rows = db.execute(
        stmt.order_by(*order).offset((params.page - 1) * params.page_size).limit(params.page_size)
    ).scalars().unique().all()
    return {"items": rows, "page": params.page, "page_size": params.page_size, "total": total}
