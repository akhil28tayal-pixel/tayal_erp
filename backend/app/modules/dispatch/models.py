"""Delivery orders (docs/02 §6, docs/06 §3).

A delivery order (DO) is one truck-load for one sales order from one godown. A truck carrying material for two
dealers simply has two DOs with the same vehicle; capacity is checked across every DO still on that vehicle.
The DO number doubles as the gate-pass number.

    PLANNED → LOADED → DISPATCHED → DELIVERED | PARTIALLY_DELIVERED
    PLANNED/LOADED → CANCELLED
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, CompanyMixin, IdMixin, TimestampMixin
from app.modules.masters.models import BaseQty, Qty, enum_check

DO_STATUSES = ["PLANNED", "LOADED", "DISPATCHED", "DELIVERED", "PARTIALLY_DELIVERED", "CANCELLED"]
DO_ACTIVE = ["PLANNED", "LOADED"]  # on the vehicle's plan, not yet out of the gate
DO_ON_ROAD = ["DISPATCHED"]


class DeliveryOrder(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "delivery_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "do_no"),
        enum_check("status", DO_STATUSES, "status"),
        Index("ix_delivery_orders_company_status", "company_id", "status"),
        CheckConstraint("(vehicle_id IS NULL) <> (direct_truck_id IS NULL)", name="one_vehicle"),
    )

    do_no: Mapped[str] = mapped_column(String(40), nullable=False)
    sales_order_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_orders.id"), index=True, nullable=False)
    dealer_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("dealers.id"), nullable=False)
    godown_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("godowns.id"), nullable=False)
    # Own truck from the fleet, or (direct_truck_id) the factory truck itself, delivering straight to the dealer.
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("vehicles.id"), index=True, nullable=True)
    direct_truck_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("direct_trucks.id"), index=True, nullable=True)
    driver_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("drivers.id"))
    dispatch_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PLANNED", nullable=False)
    supplier_ref: Mapped[str | None] = mapped_column(String(100))  # Direct godown: factory / SAP dispatch ref
    remarks: Mapped[str | None] = mapped_column(Text)
    planned_weight_kg: Mapped[Decimal | None] = mapped_column(Qty)
    capacity_override_reason: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recipient_name: Mapped[str | None] = mapped_column(String(120))
    recipient_phone: Mapped[str | None] = mapped_column(String(20))
    delivery_remarks: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    items: Mapped[list["DeliveryOrderItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="DeliveryOrderItem.line_no")


class DeliveryOrderItem(IdMixin, Base):
    __tablename__ = "delivery_order_items"
    __table_args__ = (
        CheckConstraint("planned_base > 0", name="planned_positive"),
        CheckConstraint("delivered_base + short_base + damaged_base <= dispatched_base", name="pod_le_dispatched"),
    )

    do_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("delivery_orders.id", ondelete="CASCADE"), index=True, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_order_items.id"), index=True, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), nullable=False)  # the order line's unit
    planned_qty: Mapped[Decimal] = mapped_column(Qty, nullable=False)
    planned_base: Mapped[Decimal] = mapped_column(BaseQty, nullable=False)
    loaded_base: Mapped[Decimal | None] = mapped_column(BaseQty)
    dispatched_base: Mapped[Decimal] = mapped_column(BaseQty, default=Decimal("0"), nullable=False)
    delivered_base: Mapped[Decimal] = mapped_column(BaseQty, default=Decimal("0"), nullable=False)
    short_base: Mapped[Decimal] = mapped_column(BaseQty, default=Decimal("0"), nullable=False)
    damaged_base: Mapped[Decimal] = mapped_column(BaseQty, default=Decimal("0"), nullable=False)
    actual_weight_kg: Mapped[Decimal | None] = mapped_column(Qty)  # weighbridge, TMT


class DeliveryEvent(IdMixin, Base):
    __tablename__ = "delivery_events"

    do_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("delivery_orders.id", ondelete="CASCADE"), index=True, nullable=False)
    event: Mapped[str] = mapped_column(String(24), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
