import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CompanyMixin, IdMixin
from app.modules.masters.models import enum_check


class TallySyncRun(IdMixin, CompanyMixin, Base):
    """One pull of masters and balances from Tally, started from the web or the command line."""

    __tablename__ = "tally_sync_runs"
    __table_args__ = (
        enum_check("status", ["RUNNING", "SUCCESS", "FAILED"], "status"),
        Index("ix_tally_sync_runs_company_started", "company_id", "started_at"),
    )

    tally_url: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    summary: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


TALLY_SALE_STATUSES = ["PENDING", "FAILED", "WAITING_APPROVAL", "APPROVED", "REJECTED"]


class TallySale(IdMixin, CompanyMixin, Base):
    """A delivered order sent to Tally as an *Optional* Sales voucher. Tally keeps optional vouchers out of stock
    and the dealer's balance until an admin opens it in Tally and marks it Regular (approved) — deleting it there
    rejects it. The ERP reads that state back.

        PENDING → (push) WAITING_APPROVAL → APPROVED | REJECTED
        PENDING → FAILED (Tally not reachable / refused) → retried
    """

    __tablename__ = "tally_sales"
    __table_args__ = (
        enum_check("status", TALLY_SALE_STATUSES, "status"),
        Index("ix_tally_sales_company_status", "company_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_orders.id"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    voucher_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount: Mapped[str | None] = mapped_column(String(30))
    tally_master_id: Mapped[str | None] = mapped_column(String(40))
    tally_voucher_no: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str | None] = mapped_column(Text)
    xml: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
