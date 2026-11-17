"""WhatsApp order messages → draft orders.

A message comes in (pasted / shared by staff, or sent by a dealer to the business number), the reader works out
dealer + materials, and if it found both a draft order is made for staff to check and book. Otherwise the message
waits as "needs help" with whatever was understood, and staff finish it in the order form."""

import hashlib
import hmac
import traceback
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import Ctx
from app.core.errors import AppError
from app.core.permissions import ALL
from app.modules.masters.models import Company, Dealer
from app.modules.masters.service import assigned_dealer_ids_subquery
from app.modules.orders import service as orders
from app.modules.orders.schemas import OrderCreate, OrderLineIn
from app.modules.platform import notify
from app.modules.wa_orders import reader
from app.modules.wa_orders.models import WhatsAppOrderMessage


def client_ref(msg: WhatsAppOrderMessage) -> str:
    """The order made from this message carries this reference, so it is made once and can be found again."""
    return f"wa-{msg.id}"


def system_ctx(company_id: uuid.UUID) -> Ctx:
    """For messages that arrive by themselves (webhook): no person is acting."""
    who = SimpleNamespace(id=None, employee_id=None, full_name="WhatsApp")
    return Ctx(user=who, company_id=company_id, permissions=set(ALL) - {"scope.godown_restricted", "scope.own_trips_only"})  # type: ignore[arg-type]


def add(db: Session, company_id: uuid.UUID, text: str, source: str, actor_id=None, from_phone=None, from_name=None,
        wa_message_id=None, received_at: datetime | None = None) -> WhatsAppOrderMessage:
    msg = WhatsAppOrderMessage(company_id=company_id, source=source, text=text.strip()[:4000], from_phone=from_phone,
                               from_name=from_name, wa_message_id=wa_message_id,
                               received_at=received_at or datetime.now(UTC), status="READING", created_by=actor_id)
    db.add(msg)
    db.flush()
    return msg


def process(db: Session, ctx: Ctx, msg: WhatsAppOrderMessage) -> WhatsAppOrderMessage:
    """Read the message and, when dealer and materials are clear, make the draft order."""
    only = None if ctx.has("dealers.view_all") else assigned_dealer_ids_subquery(ctx.user.employee_id)
    try:
        reading, how = reader.read(db, msg.company_id, msg.text, msg.from_phone, only_dealers=only)
    except reader.ReaderError as e:
        msg.status, msg.note, msg.reader = "FAILED", str(e), "AI"
        return msg
    msg.reading, msg.reader = reading, how
    msg.dealer_id = uuid.UUID(reading["dealer_id"]) if reading.get("dealer_id") else None
    if not reading["is_order"]:
        msg.status, msg.note = "NOT_ORDER", "Does not look like an order"
        return msg
    lines = [ln for ln in reading["lines"] if ln["product_id"] and Decimal(ln["qty"]) > 0]
    missing = []
    if msg.dealer_id is None:
        missing.append("which dealer")
    if not lines:
        missing.append("which material and how much")
    if len(lines) < len(reading["lines"]):
        missing.append("some items were not clear")
    if msg.dealer_id is None or not lines:
        msg.status, msg.note = "NEEDS_HELP", "Not sure: " + ", ".join(missing)
        return msg
    try:
        body = order_body(msg, reading, lines)
    except ValidationError:
        msg.status, msg.note = "NEEDS_HELP", "Some quantities or rates could not be used"
        return msg
    try:
        with db.begin_nested():
            order = orders.create_order(db, ctx, body, submit=False, confirm_duplicate=False)
            order.client_ref = body.client_ref
    except (AppError, ValidationError) as e:
        msg.status = "NEEDS_HELP"
        msg.note = e.detail if isinstance(e, AppError) else "Some quantities or rates could not be used"
        return msg
    msg.order_id, msg.status = order.id, "DRAFTED"
    msg.note = ("Check: " + "; ".join(reading["unclear"])) if reading["unclear"] or missing else None
    write_audit(db, action="order.from_whatsapp", entity_type="sales_orders", entity_id=order.id,
                actor_id=msg.created_by, company_id=msg.company_id, after={"message": str(msg.id), "reader": how})
    return msg


def order_body(msg: WhatsAppOrderMessage, reading: dict, lines: list[dict]) -> OrderCreate:
    notes = [x for x in (reading.get("notes"), *(f"Check: {u}" for u in reading.get("unclear") or [])) if x]
    notes.append(f"From WhatsApp{f' ({msg.from_name})' if msg.from_name else ''}: “{msg.text.strip()[:300]}”")
    return OrderCreate(
        dealer_id=uuid.UUID(reading["dealer_id"]), source="WHATSAPP",
        site_name=(reading.get("site") or None) and reading["site"][:200],
        requested_delivery_date=date.fromisoformat(reading["delivery_date"]) if reading.get("delivery_date") else None,
        priority="URGENT" if reading.get("urgent") else "NORMAL",
        special_instructions="\n".join(notes)[:2000],
        lines=[OrderLineIn(product_id=uuid.UUID(ln["product_id"]), qty=Decimal(ln["qty"]), uom=ln["unit"],
                           rate=Decimal(ln["rate"]) if ln.get("rate") else None) for ln in lines],
        submit=False, client_ref=client_ref(msg),
    )


def tell_staff(db: Session, msg: WhatsAppOrderMessage) -> None:
    """New message from a dealer: the people who book / approve orders hear about it."""
    if msg.status not in ("DRAFTED", "NEEDS_HELP"):
        return
    from app.modules.identity.models import User
    from app.modules.masters.models import DealerAssignment

    who = (msg.reading or {}).get("dealer_name") or msg.from_name or msg.from_phone or "a dealer"
    users = notify.users_with(db, msg.company_id, "orders.approve")
    if msg.dealer_id:
        emp = db.execute(select(DealerAssignment.employee_id).where(DealerAssignment.dealer_id == msg.dealer_id,
                                                                    DealerAssignment.kind == "SALES",
                                                                    DealerAssignment.valid_to.is_(None))).scalars()
        users |= set(db.execute(select(User.id).where(User.employee_id.in_(list(emp)))).scalars())
    title = f"WhatsApp order: {who}" if msg.status == "DRAFTED" else f"WhatsApp message needs a look: {who}"
    summary = ", ".join(f"{float(ln['qty']):g} {(ln['unit'] or '').lower()} {ln['product_name'] or ln['said']}"
                        for ln in (msg.reading or {}).get("lines", [])[:3])
    link = f"/orders/{msg.order_id}" if msg.order_id else "/whatsapp-orders"
    notify.send(db, msg.company_id, users, "whatsapp.order", title, summary or msg.text[:120], link,
                exclude=msg.created_by)


# ------------------------------------------------------------------ webhook from Meta
def signature_ok(body: bytes, header: str | None) -> bool:
    secret = get_settings().whatsapp_app_secret
    if not secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.removeprefix("sha256="))


def webhook_company(db: Session) -> Company | None:
    code = get_settings().whatsapp_company_code
    q = select(Company).where(Company.is_active.is_(True))
    if code:
        return db.execute(q.where(Company.code == code)).scalar_one_or_none()
    rows = db.execute(q).scalars().all()
    return rows[0] if len(rows) == 1 else None


def handle_webhook(payload: dict) -> dict:
    """Store and read incoming text messages; update delivery / read ticks of messages we sent."""
    from app.modules.collections.models import DealerMessage

    db = SessionLocal()
    out = {"messages": 0, "statuses": 0}
    try:
        company = webhook_company(db)
        if company is None:
            return out
        my_number = get_settings().whatsapp_phone_number_id
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                v = change.get("value") or {}
                if my_number and (v.get("metadata") or {}).get("phone_number_id") not in (None, my_number):
                    continue
                names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in v.get("contacts") or []}
                for st in v.get("statuses") or []:  # ticks for our balance / delivery messages
                    m = db.execute(select(DealerMessage).where(DealerMessage.provider_message_id == st.get("id"))
                                   ).scalar_one_or_none()
                    s = {"delivered": "DELIVERED", "read": "READ", "failed": "FAILED"}.get(st.get("status"))
                    if m and s and not (m.status == "READ" and s == "DELIVERED"):
                        m.status = s
                        if s == "FAILED":
                            m.error = "; ".join(e.get("title", "") for e in st.get("errors") or []) or "Failed"
                        out["statuses"] += 1
                db.commit()
                for wm in v.get("messages") or []:
                    _incoming(db, company.id, wm, names)
                    out["messages"] += 1
    except Exception:  # noqa: BLE001 - Meta retries on errors; log and answer 200
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()
    return out


def _incoming(db: Session, company_id: uuid.UUID, wm: dict, names: dict) -> None:
    wid = wm.get("id")
    if wid and db.execute(select(WhatsAppOrderMessage.id).where(WhatsAppOrderMessage.company_id == company_id,
                                                                WhatsAppOrderMessage.wa_message_id == wid)).first():
        return  # Meta sent it again
    kind = wm.get("type")
    text = (wm.get("text") or {}).get("body") if kind == "text" else None
    if kind == "button":
        text = (wm.get("button") or {}).get("text")
    try:
        when = datetime.fromtimestamp(int(wm.get("timestamp")), UTC)
    except (TypeError, ValueError):
        when = datetime.now(UTC)
    phone = wm.get("from")
    msg = add(db, company_id, text or f"[{kind or 'unknown'} message — open WhatsApp to see it]", "WHATSAPP",
              from_phone=phone, from_name=names.get(phone), wa_message_id=wid, received_at=when)
    if not text:
        msg.status, msg.note = "NEEDS_HELP", ("Voice note, photo or file: listen / look in WhatsApp and book it "
                                              "by hand")
        sender = reader.dealer_by_phone(db, company_id, phone)
        msg.dealer_id = sender.id if sender else None
        msg.reading = {"is_order": True, "dealer_id": str(sender.id) if sender else None,
                       "dealer_name": sender.name if sender else None, "lines": [], "unclear": [],
                       "dealer_choices": []}
    else:
        process(db, system_ctx(company_id), msg)
    tell_staff(db, msg)
    db.commit()


def dealer_name(db: Session, dealer_id) -> str | None:
    d = db.get(Dealer, dealer_id) if dealer_id else None
    return d.name if d else None

