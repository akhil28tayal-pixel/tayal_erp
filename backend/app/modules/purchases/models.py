"""Purchases from Ambuja / Shree Excel files (ported from the Purchase Uploader app), godown stock read from
Tally, and the register of factory trucks that came straight to the Direct godown.

Ishika Traders Daily only (decision D20). Every purchase is pushed to Tally as a Purchase voucher; Tally stays the
book of record for stock, so the ERP reads godown stock back from Tally rather than keeping its own ledger.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, CompanyMixin, IdMixin, TimestampMixin
from app.modules.masters.models import BaseQty, Qty, enum_check


class PurchaseProfile(IdMixin, TimestampMixin, CompanyMixin, Base):
    """The uploader's settings for this company: Tally company, ledgers, and the mapping rules
    (product text → stock item, source/plant → godown). Same JSON shape as the app's profile."""

    __tablename__ = "purchase_profiles"
    __table_args__ = (UniqueConstraint("company_id"),)

    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Godown chosen by hand for one invoice ({invoice_id: godown}); kept until the file is cleared.
    overrides: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class PurchaseFile(IdMixin, CompanyMixin, Base):
    """An uploaded Ambuja 'Invoice details' / Shree 'Invoice Summary Report' file on the working table."""

    __tablename__ = "purchase_files"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    supplier: Mapped[str | None] = mapped_column(String(20))
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = still on the table


class PurchasePost(IdMixin, CompanyMixin, Base):
    """One invoice pushed to Tally (the app's posted.json). Stops the same invoice being pushed twice."""

    __tablename__ = "purchase_posts"
    __table_args__ = (UniqueConstraint("company_id", "tally_company", "invoice_key"),)

    tally_company: Mapped[str] = mapped_column(String(200), nullable=False)
    invoice_key: Mapped[str] = mapped_column(String(120), nullable=False)  # "ambuja:NJAM0726…"
    supplier: Mapped[str] = mapped_column(String(20), nullable=False)
    invoice_no: Mapped[str] = mapped_column(String(80), nullable=False)
    invoice_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    godown: Mapped[str | None] = mapped_column(String(200))
    truck_no: Mapped[str | None] = mapped_column(String(40))
    vchid: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(10), default="ERP", nullable=False)  # ERP | APP (imported log)
    posted_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


DIRECT_TRUCK_STATUSES = ["OPEN", "CLOSED"]


class DirectTruck(IdMixin, TimestampMixin, CompanyMixin, Base):
    """A factory truck whose purchase went to the Direct godown: its load is delivered to dealers straight
    from the truck. Created when such a purchase is pushed to Tally (or added from an invoice already there)."""

    __tablename__ = "direct_trucks"
    __table_args__ = (
        UniqueConstraint("company_id", "invoice_key"),
        enum_check("status", DIRECT_TRUCK_STATUSES, "status"),
        Index("ix_direct_trucks_company_status", "company_id", "status"),
    )

    invoice_key: Mapped[str] = mapped_column(String(120), nullable=False)
    supplier: Mapped[str] = mapped_column(String(20), nullable=False)
    invoice_no: Mapped[str] = mapped_column(String(80), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    truck_no: Mapped[str] = mapped_column(String(40), nullable=False)
    godown_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("godowns.id"), nullable=False)
    source: Mapped[str | None] = mapped_column(String(200))  # plant / depot on the invoice
    ref: Mapped[str | None] = mapped_column(String(80))  # SAP delivery / document no.
    status: Mapped[str] = mapped_column(String(10), default="OPEN", nullable=False)
    close_note: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["DirectTruckItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class DirectTruckItem(IdMixin, Base):
    __tablename__ = "direct_truck_items"

    truck_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("direct_trucks.id", ondelete="CASCADE"), index=True, nullable=False)
    tally_item: Mapped[str] = mapped_column(String(200), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("products.id"))
    qty: Mapped[Decimal] = mapped_column(Qty, nullable=False)  # as invoiced, in bags
    uom: Mapped[str] = mapped_column(String(10), nullable=False)  # ERP unit of `qty` (BAG)
    base_qty: Mapped[Decimal | None] = mapped_column(BaseQty)  # in the product's base unit, when convertible


STOCK_RUN_STATUSES = ["RUNNING", "SUCCESS", "FAILED"]


class StockSnapshot(IdMixin, CompanyMixin, Base):
    """One read of godown-wise stock from Tally. Only the latest successful one is kept with its rows."""

    __tablename__ = "stock_snapshots"
    __table_args__ = (enum_check("status", STOCK_RUN_STATUSES, "status"),)

    status: Mapped[str] = mapped_column(String(10), default="RUNNING", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))  # button | tally_sync | purchase_push
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))


class GodownStock(IdMixin, CompanyMixin, Base):
    __tablename__ = "godown_stock"
    __table_args__ = (Index("ix_godown_stock_lookup", "company_id", "godown_id", "product_id"),)

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stock_snapshots.id", ondelete="CASCADE"), index=True, nullable=False)
    tally_godown: Mapped[str] = mapped_column(String(200), nullable=False)
    godown_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("godowns.id"))
    tally_item: Mapped[str] = mapped_column(String(200), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("products.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)  # in Tally's unit (can be negative)
    tally_unit: Mapped[str | None] = mapped_column(String(20))
    base_qty: Mapped[Decimal | None] = mapped_column(BaseQty)  # in the product's base unit, when convertible
