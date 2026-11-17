import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CompanyMixin, IdMixin, TimestampMixin
from app.modules.masters.models import enum_check

WA_STATUSES = ["READING", "DRAFTED", "NEEDS_HELP", "NOT_ORDER", "FAILED", "IGNORED"]


class WhatsAppOrderMessage(IdMixin, TimestampMixin, CompanyMixin, Base):
    """A dealer's order message: pasted / shared by staff, or received on the business number. The reader turns it
    into a draft order (DRAFTED), or keeps what it understood for staff to finish (NEEDS_HELP)."""

    __tablename__ = "wa_order_messages"
    __table_args__ = (
        UniqueConstraint("company_id", "wa_message_id"),
        enum_check("status", WA_STATUSES, "status"),
        enum_check("source", ["PASTE", "WHATSAPP"], "source"),
        Index("ix_wa_order_messages_company_received", "company_id", "received_at"),
    )

    source: Mapped[str] = mapped_column(String(10), nullable=False)
    wa_message_id: Mapped[str | None] = mapped_column(String(120))  # Meta's id (webhook retries are ignored)
    from_phone: Mapped[str | None] = mapped_column(String(20))
    from_name: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="READING")
    dealer_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("dealers.id"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sales_orders.id"))
    reading: Mapped[dict | None] = mapped_column(JSONB)  # what was understood (dealer, lines, date, notes)
    reader: Mapped[str | None] = mapped_column(String(20))  # AI | RULES
    note: Mapped[str | None] = mapped_column(Text)  # plain-language reason (why it needs help, error)
