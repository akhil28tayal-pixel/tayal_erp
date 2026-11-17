"""Sales orders (docs/02 §4, docs/06 §1).

Stock reservation arrives with the inventory ledger (Step 4). Until then an order line carries the godown it
should be loaded from, and dispatch quantities are checked against the order, never against a stock figure.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, CompanyMixin, IdMixin, TimestampMixin
from app.modules.masters.models import BaseQty, Factor, Money, Qty, Rate, enum_check

ORDER_STATUSES = [
    "DRAFT", "UNDER_REVIEW", "ON_HOLD", "APPROVED", "PARTIALLY_DISPATCHED", "DISPATCHED",
    "PARTIALLY_DELIVERED", "DELIVERED", "CLOSED", "REJECTED", "CANCELLED",
]
# Statuses whose remaining quantity still counts towards the dealer's credit exposure / dispatch board.
OPEN_FULFILMENT = ["APPROVED", "PARTIALLY_DISPATCHED", "DISPATCHED", "PARTIALLY_DELIVERED"]
HOLD_KINDS = ["CREDIT", "STALE_BALANCE", "MANUAL"]


class SalesOrder(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "order_no"),
        UniqueConstraint("company_id", "client_ref", name="uq_sales_orders_client_ref"),
        enum_check("status", ORDER_STATUSES, "status"),
        enum_check("source", ["OFFICE", "SALES_EXEC", "PHONE", "WHATSAPP"], "source"),
        enum_check("priority", ["NORMAL", "URGENT"], "priority"),
        Index("ix_sales_orders_company_status_date", "company_id", "status", "order_date"),
        Index("ix_sales_orders_dealer_date", "dealer_id", "order_date"),
    )

    order_no: Mapped[str | None] = mapped_column(String(40))  # given on submit; drafts have none
    # Set by the phone when an order is booked with a weak network and sent later: the same booking sent twice
    # gives back the first order instead of a duplicate.
    client_ref: Mapped[str | None] = mapped_column(String(64))
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    dealer_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("dealers.id"), nullable=False)
    delivery_address_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("dealer_addresses.id"))
    site_name: Mapped[str | None] = mapped_column(String(200))
    sales_exec_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("employees.id"))
    source: Mapped[str] = mapped_column(String(12), default="OFFICE", nullable=False)
    priority: Mapped[str] = mapped_column(String(8), default="NORMAL", nullable=False)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    special_instructions: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    hold_kind: Mapped[str | None] = mapped_column(String(16))
    hold_reason: Mapped[str | None] = mapped_column(Text)
    # Why a human has to look at it: [{"code": "RATE_BELOW_MIN", "detail": "..."}]
    review_reasons: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    dedupe_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    items: Mapped[list["SalesOrderItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="SalesOrderItem.line_no")


class SalesOrderItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "sales_order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no"),
        CheckConstraint("base_qty > 0", name="qty_positive"),
        CheckConstraint("qty_cancelled_base >= 0 AND qty_cancelled_base <= base_qty", name="cancel_range"),
        CheckConstraint("qty_dispatched_base <= base_qty - qty_cancelled_base", name="dispatch_le_open"),
        CheckConstraint("qty_delivered_base <= qty_dispatched_base", name="delivered_le_dispatched"),
        enum_check("status", ["OPEN", "CANCELLED", "FULFILLED"], "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    godown_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("godowns.id"))
    entered_qty: Mapped[Decimal] = mapped_column(Qty, nullable=False)
    entered_uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), nullable=False)
    base_qty: Mapped[Decimal] = mapped_column(BaseQty, nullable=False)
    base_uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Factor, nullable=False)  # entered → base, frozen
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False)
    rate_uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), nullable=False)
    rate_qty: Mapped[Decimal] = mapped_column(BaseQty, nullable=False)  # quantity expressed in rate_uom
    list_rate: Mapped[Decimal | None] = mapped_column(Rate)  # the dealer's authorised rate, if any
    min_rate: Mapped[Decimal | None] = mapped_column(Rate)
    rate_source: Mapped[str] = mapped_column(String(20), default="MANUAL", nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    qty_cancelled_base: Mapped[Decimal] = mapped_column(BaseQty, default=Decimal("0"), nullable=False)
    qty_dispatched_base: Mapped[Decimal] = mapped_column(BaseQty, default=Decimal("0"), nullable=False)
    qty_delivered_base: Mapped[Decimal] = mapped_column(BaseQty, default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="OPEN", nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(300))


class OrderStatusHistory(IdMixin, Base):
    __tablename__ = "order_status_history"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderCreditCheck(IdMixin, Base):
    """What the credit position looked like when the order was submitted/approved — kept for every check."""

    __tablename__ = "order_credit_checks"
    __table_args__ = (enum_check("result", ["PASS", "WARN", "HOLD"], "result"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stage: Mapped[str] = mapped_column(String(10), nullable=False)  # SUBMIT | APPROVE
    tally_balance: Mapped[Decimal | None] = mapped_column(Money)
    balance_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False)
    credit_limit: Mapped[Decimal | None] = mapped_column(Money)
    open_order_exposure: Mapped[Decimal] = mapped_column(Money, nullable=False)
    this_order_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    available_credit: Mapped[Decimal | None] = mapped_column(Money)
    result: Mapped[str] = mapped_column(String(4), nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
