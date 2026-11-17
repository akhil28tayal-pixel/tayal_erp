"""Messages sent to dealers (balance reminders today; payment receipts and follow-ups later)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CompanyMixin, IdMixin, TimestampMixin
from app.modules.masters.models import Money, enum_check


class DealerMessage(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "dealer_messages"
    __table_args__ = (
        enum_check("channel", ["WHATSAPP"], "channel"),
        enum_check("kind", ["BALANCE", "DELIVERY"], "kind"),
        # CLICK_TO_CHAT: the ERP opens WhatsApp with the text filled in; the person presses send.
        # CLOUD_API: sent by the WhatsApp Business Platform (not connected yet).
        enum_check("provider", ["CLICK_TO_CHAT", "CLOUD_API"], "provider"),
        enum_check("status", ["OPENED", "SENT", "DELIVERED", "READ", "FAILED"], "status"),
        Index("ix_dealer_messages_dealer_created", "dealer_id", "created_at"),
    )

    dealer_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("dealers.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(12), default="WHATSAPP", nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    balance: Mapped[Decimal | None] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(100))
    ref_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))  # DELIVERY: the delivery order
    error: Mapped[str | None] = mapped_column(Text)


class WhatsAppBulkRun(IdMixin, CompanyMixin, Base):
    """One "send balance to all dealers" run. Progress is updated as each message goes out."""

    __tablename__ = "whatsapp_bulk_runs"
    __table_args__ = (enum_check("status", ["RUNNING", "DONE", "FAILED"], "status"),)

    status: Mapped[str] = mapped_column(String(10), nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)  # [{dealer, phone, error}]
    error: Mapped[str | None] = mapped_column(Text)
