import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import Ctx, require
from app.core.errors import AppError, not_found
from app.modules.masters.service import assigned_dealer_ids_subquery
from app.modules.orders.models import SalesOrder
from app.modules.wa_orders import service
from app.modules.wa_orders.models import WhatsAppOrderMessage

router = APIRouter(tags=["whatsapp orders"])


class ReadIn(BaseModel):
    text: str = Field(min_length=2, max_length=4000)
    from_phone: str | None = Field(default=None, max_length=20, description="Dealer's number, if known")


class MessageOut(BaseModel):
    id: uuid.UUID
    source: str
    from_phone: str | None
    from_name: str | None
    text: str
    received_at: datetime
    status: str  # READING DRAFTED NEEDS_HELP NOT_ORDER FAILED IGNORED, or BOOKED once the order is booked
    note: str | None
    reader: str | None
    reading: dict | None
    dealer_id: uuid.UUID | None
    dealer_name: str | None
    order_id: uuid.UUID | None
    order_no: str | None
    order_status: str | None
    client_ref: str


class ListOut(BaseModel):
    ai_ready: bool
    webhook_ready: bool
    counts: dict[str, int]
    items: list[MessageOut]


def _out(db: Session, m: WhatsAppOrderMessage) -> dict:
    order = db.get(SalesOrder, m.order_id) if m.order_id else None
    if order is None:  # finished by hand in the order form
        order = db.execute(select(SalesOrder).where(SalesOrder.company_id == m.company_id,
                                                    SalesOrder.client_ref == service.client_ref(m))).scalar_one_or_none()
    status = m.status
    if order is not None and order.status not in ("DRAFT", "CANCELLED"):
        status = "BOOKED"
    return {"id": m.id, "source": m.source, "from_phone": m.from_phone, "from_name": m.from_name, "text": m.text,
            "received_at": m.received_at, "status": status, "note": m.note, "reader": m.reader, "reading": m.reading,
            "dealer_id": m.dealer_id, "dealer_name": service.dealer_name(db, m.dealer_id),
            "order_id": order.id if order else None, "order_no": order.order_no if order else None,
            "order_status": order.status if order else None, "client_ref": service.client_ref(m)}


def _visible(stmt, ctx: Ctx):
    if ctx.has("dealers.view_all"):
        return stmt
    return stmt.where(or_(WhatsAppOrderMessage.created_by == ctx.user_id,
                          WhatsAppOrderMessage.dealer_id.in_(assigned_dealer_ids_subquery(ctx.user.employee_id))))


def _get(db: Session, ctx: Ctx, msg_id: uuid.UUID) -> WhatsAppOrderMessage:
    m = db.execute(_visible(select(WhatsAppOrderMessage).where(WhatsAppOrderMessage.id == msg_id,
                                                               WhatsAppOrderMessage.company_id == ctx.company_id),
                            ctx)).scalar_one_or_none()
    if m is None:
        raise not_found("Message")
    return m


@router.post("/whatsapp-orders/read", response_model=MessageOut, status_code=201)
def read_message(body: ReadIn, ctx: Ctx = Depends(require("orders.create")), db: Session = Depends(get_db)):
    """Paste (or share from WhatsApp) a dealer's message: it is read and, if clear, made into a draft order."""
    m = service.add(db, ctx.company_id, body.text, "PASTE", actor_id=ctx.user_id, from_phone=body.from_phone)
    service.process(db, ctx, m)
    db.commit()
    return _out(db, m)


@router.get("/whatsapp-orders", response_model=ListOut)
def list_messages(status: str | None = None, limit: int = Query(100, ge=1, le=300),
                  ctx: Ctx = Depends(require("orders.view")), db: Session = Depends(get_db)):
    base = _visible(select(WhatsAppOrderMessage).where(WhatsAppOrderMessage.company_id == ctx.company_id), ctx)
    rows = db.execute(base.order_by(WhatsAppOrderMessage.received_at.desc()).limit(300)).scalars().all()
    items = [_out(db, m) for m in rows]
    counts: dict[str, int] = {}
    for x in items:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    if status == "OPEN":
        items = [x for x in items if x["status"] in ("DRAFTED", "NEEDS_HELP", "FAILED")]
    elif status:
        items = [x for x in items if x["status"] == status]
    s = get_settings()
    return {"ai_ready": bool(s.anthropic_api_key),
            "webhook_ready": bool(s.whatsapp_verify_token and s.whatsapp_app_secret),
            "counts": counts, "items": items[:limit]}


@router.get("/whatsapp-orders/{msg_id}", response_model=MessageOut)
def get_message(msg_id: uuid.UUID, ctx: Ctx = Depends(require("orders.view")), db: Session = Depends(get_db)):
    return _out(db, _get(db, ctx, msg_id))


@router.post("/whatsapp-orders/{msg_id}/ignore", response_model=MessageOut)
def ignore(msg_id: uuid.UUID, ctx: Ctx = Depends(require("orders.create")), db: Session = Depends(get_db)):
    m = _get(db, ctx, msg_id)
    order = db.get(SalesOrder, m.order_id) if m.order_id else None
    if order is not None and order.status != "DRAFT":
        raise AppError(409, "ALREADY_BOOKED", "This order is already booked")
    if order is not None:  # throw away the draft made from it
        from app.modules.orders import service as orders

        orders._set_status(db, order, "CANCELLED", "cancel", ctx, reason="WhatsApp message set aside")
        m.order_id = None
    m.status = "IGNORED"
    db.commit()
    return _out(db, m)


@router.post("/whatsapp-orders/{msg_id}/reread", response_model=MessageOut)
def reread(msg_id: uuid.UUID, ctx: Ctx = Depends(require("orders.create")), db: Session = Depends(get_db)):
    """Read it again (e.g. after adding the dealer's mobile number or switching a product on)."""
    m = _get(db, ctx, msg_id)
    if m.order_id is not None:
        raise AppError(409, "ALREADY_DRAFTED", "A draft order was already made from this message")
    m.status, m.note = "READING", None
    service.process(db, ctx, m)
    db.commit()
    return _out(db, m)


# ------------------------------------------------------------------ Meta webhook (no login: checked by signature)
@router.get("/whatsapp/webhook", include_in_schema=False)
def verify(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
           challenge: str = Query("", alias="hub.challenge")):
    want = get_settings().whatsapp_verify_token
    if mode == "subscribe" and want and token == want:
        return PlainTextResponse(challenge)
    raise AppError(403, "FORBIDDEN", "Verification failed")


@router.post("/whatsapp/webhook", include_in_schema=False)
async def receive(request: Request, background: BackgroundTasks):
    raw = await request.body()
    if not service.signature_ok(raw, request.headers.get("X-Hub-Signature-256")):
        raise AppError(403, "FORBIDDEN", "Bad signature")
    try:
        payload = json.loads(raw)
    except ValueError as e:
        raise AppError(400, "BAD_REQUEST", "Not JSON") from e
    background.add_task(service.handle_webhook, payload)  # answer Meta at once; read in the background
    return {"ok": True}

