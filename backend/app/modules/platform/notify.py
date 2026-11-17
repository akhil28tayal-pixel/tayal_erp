"""Notifications: who should hear about what. Each one is a row the web bell and the phone app read."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.models import RolePermission, User, UserRole
from app.modules.platform.models import Notification


def users_with(db: Session, company_id: uuid.UUID, perm: str) -> set[uuid.UUID]:
    return set(db.execute(
        select(UserRole.user_id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(User, User.id == UserRole.user_id)
        .where(UserRole.company_id == company_id, RolePermission.permission_code == perm, User.is_active.is_(True))
    ).scalars())


def send(db: Session, company_id: uuid.UUID, user_ids, kind: str, title: str, body: str | None = None,
         link: str | None = None, exclude: uuid.UUID | None = None) -> int:
    n = 0
    for uid in {u for u in user_ids if u and u != exclude}:
        db.add(Notification(company_id=company_id, user_id=uid, kind=kind, title=title[:200], body=body, link=link))
        n += 1
    return n


def _qty(order) -> str:
    """'400 bags Ambuja, 2 MT TMT 12mm' — short, for a phone notification."""
    from sqlalchemy.orm import object_session

    from app.modules.masters.models import Product

    db = object_session(order)
    parts = []
    for it in order.items[:3]:
        p = db.get(Product, it.product_id)
        q = Decimal(it.entered_qty).normalize()
        parts.append(f"{q:f} {it.entered_uom.lower()} {p.name if p else ''}".strip())
    more = len(order.items) - 3
    return ", ".join(parts) + (f" +{more} more" if more > 0 else "")


def order_status(db: Session, order, to: str, actor_id: uuid.UUID | None, reason: str | None = None) -> None:
    """Called whenever an order changes status."""
    from app.modules.masters.models import Dealer

    dealer = db.get(Dealer, order.dealer_id)
    who = dealer.name if dealer else "Dealer"
    no = order.order_no or "Order"
    link = f"/orders/{order.id}"
    co = order.company_id
    booked_by = order.created_by
    if to == "UNDER_REVIEW":
        send(db, co, users_with(db, co, "orders.approve"), "order.waiting", f"New order waiting: {who}",
             f"{no} · {_qty(order)}", link, exclude=actor_id)
    elif to == "ON_HOLD":
        perm = "orders.hold" if order.hold_kind == "MANUAL" else "credit.override"
        send(db, co, users_with(db, co, perm), "order.on_hold", f"On hold: {who}",
             f"{no} · {reason or order.hold_reason or ''}".strip(" ·"), link, exclude=actor_id)
        send(db, co, [booked_by], "order.on_hold", f"Your order is on hold: {who}", f"{no} · {reason or ''}".strip(" ·"),
             link, exclude=actor_id)
    elif to == "APPROVED":
        send(db, co, [booked_by], "order.approved", f"Approved: {who}", f"{no} · {_qty(order)}", link, exclude=actor_id)
        send(db, co, users_with(db, co, "dispatch.plan"), "order.to_dispatch", f"Ready to send: {who}",
             f"{no} · {_qty(order)}", link, exclude=actor_id)
    elif to == "REJECTED":
        send(db, co, [booked_by], "order.rejected", f"Rejected: {who}", f"{no} · {reason or ''}".strip(" ·"), link,
             exclude=actor_id)
    elif to == "DELIVERED":
        send(db, co, {booked_by} | users_with(db, co, "dispatch.plan"), "order.delivered", f"Delivered: {who}",
             f"{no} · {_qty(order)}", link, exclude=actor_id)


def tally_sale_rejected(db: Session, sale, order) -> None:
    from app.modules.masters.models import Dealer

    dealer = db.get(Dealer, order.dealer_id)
    send(db, order.company_id, users_with(db, order.company_id, "tally.sync"), "tally.rejected",
         f"Sale rejected in Tally: {dealer.name if dealer else ''}", f"{order.order_no} · {sale.error or ''}",
         "/tally-sales")
