"""WhatsApp to the dealer when a truck is delivered: what arrived, on which truck, for which order.

Sent from the business number through the Cloud API with an approved Utility template (setting
whatsapp.delivery_template, empty = off). Its body variables, in order:
{{1}} dealer  {{2}} what was delivered  {{3}} truck number  {{4}} order number  {{5}} date and time  {{6}} received by
"""

import traceback
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.modules.collections import whatsapp_cloud
from app.modules.collections.models import DealerMessage
from app.modules.platform.settings import get_setting

IST = ZoneInfo("Asia/Kolkata")
SUGGESTED_TEMPLATE = (
    "Dear {{1}}, your delivery has reached: {{2}}. Truck {{3}}, order {{4}}, delivered on {{5}}, received by {{6}}. "
    "Thank you for your business."
)


def delivered_text(db: Session, do) -> str:
    from app.modules.masters.models import Product

    parts = []
    for it in do.items:
        if it.delivered_base and it.delivered_base > 0:
            p = db.get(Product, it.product_id)
            q = Decimal(it.delivered_base).normalize()
            parts.append(f"{q:f} {(p.base_uom if p else '').lower()} {p.name if p else ''}".strip())
    return ", ".join(parts) or "nothing"


def values(db: Session, do) -> list[str]:
    from app.modules.dispatch.service import vehicle_label
    from app.modules.masters.models import Dealer
    from app.modules.orders.models import SalesOrder

    dealer = db.get(Dealer, do.dealer_id)
    order = db.get(SalesOrder, do.sales_order_id)
    when = (do.delivered_at.astimezone(IST).strftime("%d %b %Y, %I:%M %p") if do.delivered_at else "today")
    return [dealer.name.strip(), delivered_text(db, do), vehicle_label(db, do).replace(" (direct)", ""),
            order.order_no or do.do_no, when, (do.recipient_name or "site").strip()]


def send_for_do(db: Session, do_id: uuid.UUID) -> DealerMessage | None:
    """Send (once) for a delivered DO. Returns the logged message, or None when switched off / nothing to send."""
    from app.modules.collections.service import whatsapp_numbers
    from app.modules.dispatch.models import DeliveryOrder
    from app.modules.masters.models import Dealer

    do = db.get(DeliveryOrder, do_id)
    template = (get_setting(db, do.company_id, "whatsapp.delivery_template") or "").strip()
    if not template or do.status != "DELIVERED":
        return None
    if db.execute(select(DealerMessage.id).where(DealerMessage.kind == "DELIVERY", DealerMessage.ref_id == do.id,
                                                 DealerMessage.status != "FAILED")).first():
        return None  # already sent for this truck
    dealer = db.get(Dealer, do.dealer_id)
    numbers = [n["number"] for n in whatsapp_numbers(db, dealer)]
    params = values(db, do)
    body = SUGGESTED_TEMPLATE
    for i, v in enumerate(params, 1):
        body = body.replace("{{" + str(i) + "}}", v)
    msg = DealerMessage(company_id=do.company_id, dealer_id=dealer.id, channel="WHATSAPP", kind="DELIVERY",
                        provider="CLOUD_API", phone=numbers[0] if numbers else "-", body=body, status="SENT",
                        ref_id=do.id)
    db.add(msg)
    if not numbers:
        msg.status, msg.error = "FAILED", "No mobile number for this dealer"
    else:
        try:
            msg.provider_message_id = whatsapp_cloud.send_template(
                numbers[0], template, get_setting(db, do.company_id, "whatsapp.template_language"), params)
        except whatsapp_cloud.WhatsAppError as e:
            msg.status, msg.error = "FAILED", str(e)
    db.commit()
    return msg


def send_in_background(do_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        send_for_do(db, do_id)
    except Exception:  # noqa: BLE001 - a WhatsApp problem must never break delivery
        traceback.print_exc()
    finally:
        db.close()
