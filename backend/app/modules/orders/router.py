import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, require
from app.core.pagination import Page, PageParams, paginate
from app.modules.dispatch import service as dispatch_service
from app.modules.dispatch.models import DeliveryOrder
from app.modules.identity.models import User
from app.modules.masters.models import Dealer, Employee, Godown, Product, Vehicle
from app.modules.masters.service import delivery_location
from app.modules.orders import service
from app.modules.orders.models import OrderCreditCheck, OrderStatusHistory, SalesOrder
from app.modules.orders.schemas import (
    ApproveIn,
    HistoryEntryOut,
    LastOrderOut,
    OrderCreate,
    OrderIn,
    OrderOut,
    OrderSummaryOut,
    OrderUpdate,
    PreviewOut,
    ReasonIn,
    SubmitIn,
)

router = APIRouter(prefix="/orders", tags=["orders"])

ZERO = Decimal("0")


def _fmt(q: Decimal) -> str:
    q = q.normalize()
    return f"{q:f}"


def _names(db: Session, model, ids, attr: str) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {r.id: getattr(r, attr) for r in db.execute(select(model).where(model.id.in_(ids))).scalars()}


def summaries(db: Session, orders: list[SalesOrder]) -> list[dict]:
    dealers = {d.id: d for d in db.execute(select(Dealer).where(Dealer.id.in_({o.dealer_id for o in orders}))).scalars()} if orders else {}
    users = _names(db, User, [o.created_by for o in orders], "full_name")
    emps = _names(db, Employee, [o.sales_exec_id for o in orders], "full_name")
    products = _names(db, Product, [i.product_id for o in orders for i in o.items], "name")
    out = []
    for o in orders:
        d = dealers[o.dealer_id]
        parts = [f"{_fmt(i.entered_qty)} {i.entered_uom} {products.get(i.product_id, '')}" for i in o.items]
        out.append({
            "id": o.id, "order_no": o.order_no, "order_date": o.order_date, "dealer_id": o.dealer_id,
            "dealer_name": d.name, "dealer_code": d.code, "status": o.status, "hold_kind": o.hold_kind,
            "priority": o.priority, "total_amount": o.total_amount,
            "summary": "; ".join(parts[:3]) + (f" +{len(parts) - 3} more" if len(parts) > 3 else ""),
            "booked_by_name": users.get(o.created_by), "sales_exec_name": emps.get(o.sales_exec_id),
            "created_at": o.created_at,
        })
    return out


def _unit_weight(db: Session, p: Product, factor: Decimal) -> Decimal | None:
    w = service.weight_per_base(db, p)
    return (w * factor).quantize(Decimal("0.001")) if w is not None else None


def order_out(db: Session, ctx: Ctx, o: SalesOrder) -> dict:
    base = summaries(db, [o])[0]
    products = {p.id: p for p in db.execute(select(Product).where(Product.id.in_([i.product_id for i in o.items]))).scalars()}
    godowns = _names(db, Godown, [i.godown_id for i in o.items], "name")
    planned = dispatch_service.planned_base(db, [i.id for i in o.items])
    q = dispatch_service.to_qty
    items = []
    for i in o.items:
        p = products[i.product_id]
        pl = planned.get(i.id, ZERO)
        items.append({
            "id": i.id, "line_no": i.line_no, "product_id": i.product_id, "product_name": p.name,
            "product_code": p.code, "godown_id": i.godown_id, "godown_name": godowns.get(i.godown_id),
            "qty": i.entered_qty, "uom": i.entered_uom, "base_qty": i.base_qty, "base_uom": i.base_uom,
            "rate": i.rate, "rate_uom": i.rate_uom, "list_rate": i.list_rate, "min_rate": i.min_rate,
            "rate_source": i.rate_source, "amount": i.amount, "cancelled_qty": q(i, i.qty_cancelled_base),
            "dispatched_qty": q(i, i.qty_dispatched_base), "delivered_qty": q(i, i.qty_delivered_base),
            "planned_qty": q(i, pl), "remaining_qty": q(i, max(dispatch_service.remaining_base(i, pl), ZERO)),
            "weight_per_unit_kg": _unit_weight(db, p, i.conversion_factor), "status": i.status, "remarks": i.remarks,
        })
    history = db.execute(select(OrderStatusHistory).where(OrderStatusHistory.order_id == o.id)
                         .order_by(OrderStatusHistory.at, OrderStatusHistory.id)).scalars().all()
    actors = _names(db, User, [h.actor_id for h in history] + [o.approved_by], "full_name")
    checks = db.execute(select(OrderCreditCheck).where(OrderCreditCheck.order_id == o.id)
                        .order_by(OrderCreditCheck.checked_at)).scalars().all()
    dos = db.execute(select(DeliveryOrder).where(DeliveryOrder.sales_order_id == o.id)
                     .order_by(DeliveryOrder.created_at)).scalars().all()
    vehicles = _names(db, Vehicle, [d.vehicle_id for d in dos], "registration_no")
    from app.modules.integrations.tally.models import TallySale
    from app.modules.purchases.models import DirectTruck

    vehicles.update({d.id: t.truck_no for d in dos if d.direct_truck_id
                     for t in [db.get(DirectTruck, d.direct_truck_id)]})
    tally = db.execute(select(TallySale).where(TallySale.order_id == o.id)).scalar_one_or_none()
    cp = service.credit_position(db, o.company_id, db.get(Dealer, o.dealer_id), o.total_amount or ZERO, exclude_order_id=o.id)
    credit_now = {**{k: cp[k] for k in ("tally_balance", "balance_as_of", "is_stale", "credit_limit",
                                         "open_order_exposure", "this_order_amount", "available_credit", "result")},
                  "total_after": (cp["tally_balance"] or ZERO) + cp["open_order_exposure"] + cp["this_order_amount"]}
    dealer = db.get(Dealer, o.dealer_id)
    location, location_source = delivery_location(db, dealer, o.delivery_address_id)
    from app.modules.collections.service import whatsapp_numbers

    numbers = whatsapp_numbers(db, dealer)
    return {
        **base, "delivery_address_id": o.delivery_address_id, "site_name": o.site_name,
        "delivery_location": location, "delivery_location_source": location_source,
        "dealer_phone": numbers[0]["number"][2:] if numbers else None,
        "requested_delivery_date": o.requested_delivery_date, "source": o.source,
        "special_instructions": o.special_instructions, "hold_reason": o.hold_reason,
        "review_reasons": o.review_reasons, "cancel_reason": o.cancel_reason, "submitted_at": o.submitted_at,
        "approved_at": o.approved_at, "approved_by_name": actors.get(o.approved_by), "created_by": o.created_by,
        "version": o.version, "items": items,
        "history": [{"from_status": h.from_status, "to_status": h.to_status, "action": h.action, "reason": h.reason,
                     "actor_name": actors.get(h.actor_id), "at": h.at} for h in history],
        "credit_checks": checks,
        "delivery_orders": [{"id": d.id, "do_no": d.do_no, "status": d.status,
                             "vehicle_no": vehicles.get(d.vehicle_id) or vehicles.get(d.id, ""),
                             "dispatch_date": d.dispatch_date}
                            for d in dos],
        "can": service.allowed_actions(db, ctx, o),
        "credit_now": credit_now,
        "tally_status": tally.status if tally else None,
        "tally_note": (tally.error if tally and tally.status in ("FAILED", "REJECTED") else
                       (f"Tally voucher {tally.tally_voucher_no}" if tally and tally.tally_voucher_no else None)),
    }


@router.get("", response_model=Page[OrderSummaryOut])
def list_orders(params: PageParams = Depends(), status: list[str] | None = Query(None),
                dealer_id: uuid.UUID | None = None, q: str | None = None, mine: bool = False,
                date_from: date | None = None, date_to: date | None = None,
                ctx: Ctx = Depends(require("orders.view")), db: Session = Depends(get_db)):
    stmt = service.orders_visible(select(SalesOrder).where(SalesOrder.company_id == ctx.company_id), ctx)
    if status:
        stmt = stmt.where(SalesOrder.status.in_(status))
    if dealer_id:
        stmt = stmt.where(SalesOrder.dealer_id == dealer_id)
    if mine:
        stmt = stmt.where(SalesOrder.created_by == ctx.user_id)
    if date_from:
        stmt = stmt.where(SalesOrder.order_date >= date_from)
    if date_to:
        stmt = stmt.where(SalesOrder.order_date <= date_to)
    if q:
        stmt = stmt.join(Dealer, Dealer.id == SalesOrder.dealer_id).where(
            Dealer.name.ilike(f"%{q}%") | SalesOrder.order_no.ilike(f"%{q}%") | SalesOrder.site_name.ilike(f"%{q}%"))
    page = paginate(db, stmt, params, {"order_date": SalesOrder.order_date, "created_at": SalesOrder.created_at,
                                       "total_amount": SalesOrder.total_amount},
                    [SalesOrder.created_at.desc()])
    return {**page, "items": summaries(db, page["items"])}


@router.post("/preview", response_model=PreviewOut)
def preview(body: OrderIn, ctx: Ctx = Depends(require("orders.create")), db: Session = Depends(get_db)):
    """Price the lines and show the dealer's credit position without saving anything."""
    dealer, lines = service.price_order(db, ctx, body)
    total = sum((pl.amount for pl in lines), ZERO)
    weights = [pl.weight_kg for pl in lines]
    pos = service.credit_position(db, ctx.company_id, dealer, total)
    db.rollback()
    return {
        "lines": [{"line_no": pl.line_no, "product_id": pl.product.id, "product_name": pl.product.name,
                   "godown_id": pl.godown_id, "qty": pl.qty, "uom": pl.uom, "base_qty": pl.base_qty,
                   "base_uom": pl.product.base_uom, "rate": pl.rate, "rate_uom": pl.rate_uom, "rate_qty": pl.rate_qty,
                   "list_rate": pl.list_rate, "min_rate": pl.min_rate, "rate_source": pl.rate_source,
                   "amount": pl.amount, "weight_kg": pl.weight_kg, "flags": pl.flags} for pl in lines],
        "total_amount": total,
        "total_weight_kg": None if None in weights else sum(weights, ZERO),
        "credit": pos,
    }


@router.post("", response_model=OrderOut, status_code=201)
def create(body: OrderCreate, ctx: Ctx = Depends(require("orders.create")), db: Session = Depends(get_db)):
    if body.client_ref:  # sent again by the phone after a weak network: give back the order already made
        done = db.execute(select(SalesOrder).where(SalesOrder.company_id == ctx.company_id,
                                                   SalesOrder.client_ref == body.client_ref)).scalar_one_or_none()
        if done is not None:
            return order_out(db, ctx, service.get_order(db, ctx, done.id))
    order = service.create_order(db, ctx, body, body.submit, body.confirm_duplicate)
    order.client_ref = body.client_ref
    if body.client_ref and body.client_ref.startswith("wa-"):  # finished by hand from a WhatsApp message
        from app.modules.wa_orders.models import WhatsAppOrderMessage

        try:
            m = db.get(WhatsAppOrderMessage, uuid.UUID(body.client_ref[3:]))
        except ValueError:
            m = None
        if m is not None and m.company_id == ctx.company_id and m.order_id is None:
            m.order_id, m.dealer_id = order.id, order.dealer_id
    try:
        db.commit()
    except IntegrityError:  # the same booking arrived twice at the same moment
        db.rollback()
        done = db.execute(select(SalesOrder).where(SalesOrder.company_id == ctx.company_id,
                                                   SalesOrder.client_ref == body.client_ref)).scalar_one()
        return order_out(db, ctx, service.get_order(db, ctx, done.id))
    return order_out(db, ctx, order)


@router.get("/last", response_model=LastOrderOut | None)
def last_order(dealer_id: uuid.UUID, ctx: Ctx = Depends(require("orders.create")), db: Session = Depends(get_db)):
    """The dealer's most recent order, to book the same again ("Same as last time")."""
    service.dealer_visible(db, ctx, dealer_id)
    o = db.execute(select(SalesOrder).where(
        SalesOrder.company_id == ctx.company_id, SalesOrder.dealer_id == dealer_id,
        SalesOrder.status.notin_(["DRAFT", "CANCELLED"])).order_by(SalesOrder.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if o is None:
        return None
    lines = []
    for it in sorted(o.items, key=lambda i: i.line_no):
        p = db.get(Product, it.product_id)
        g = db.get(Godown, it.godown_id) if it.godown_id else None
        lines.append({"product_id": it.product_id, "product_name": p.name, "qty": it.entered_qty,
                      "uom": it.entered_uom, "godown_id": it.godown_id, "godown_name": g.name if g else None,
                      "rate": it.rate})
    return {"order_id": o.id, "order_no": o.order_no, "order_date": o.order_date, "status": o.status,
            "site_name": o.site_name, "lines": lines}


@router.get("/{order_id}/history", response_model=list[HistoryEntryOut])
def history(order_id: uuid.UUID, ctx: Ctx = Depends(require("orders.view")), db: Session = Depends(get_db)):
    """Everything that happened to the order, oldest first: booked, approved, trucks planned / loaded /
    sent / delivered, Tally, WhatsApp."""
    from app.modules.collections.models import DealerMessage
    from app.modules.dispatch.models import DeliveryEvent
    from app.modules.integrations.tally.models import TallySale

    o = service.get_order(db, ctx, order_id)
    names: dict = {}

    def who(uid):
        if uid is None:
            return None
        if uid not in names:
            u = db.get(User, uid)
            names[uid] = u.full_name if u else None
        return names[uid]

    words = {"submit": "Booked", "auto_approve": "Approved automatically", "approve": "Approved",
             "credit_hold": "Put on hold (credit)", "hold": "Put on hold", "release": "Released from hold",
             "reject": "Rejected", "cancel": "Cancelled", "close": "Closed", "fulfilment": None,
             "qty_change": "Quantity changed at dispatch", "rest_closed": "Rest of the order closed"}
    status_words = {"PARTIALLY_DISPATCHED": "Partly sent", "DISPATCHED": "All trucks sent",
                    "PARTIALLY_DELIVERED": "Partly delivered", "DELIVERED": "Fully delivered",
                    "ON_HOLD": "On hold", "UNDER_REVIEW": "Waiting for approval"}
    out = [{"at": o.created_at, "who": who(o.created_by), "what": "Created", "kind": "order",
            "detail": f"{len(o.items)} line(s), ₹{o.total_amount:,.2f}"}]
    for h in db.execute(select(OrderStatusHistory).where(OrderStatusHistory.order_id == o.id)
                        .order_by(OrderStatusHistory.at)).scalars():
        if h.action == "create":
            continue  # the "Created" line above
        what = words.get(h.action, h.action.replace("_", " ").capitalize())
        if h.action == "submit" and h.to_status == "ON_HOLD":
            what = "Booked — put on hold"
        if what is None:
            what = status_words.get(h.to_status, h.to_status.replace("_", " ").capitalize())
        out.append({"at": h.at, "who": who(h.actor_id), "what": what, "detail": h.reason, "kind": "order"})
    dos = {d.id: d for d in db.execute(select(DeliveryOrder).where(DeliveryOrder.sales_order_id == o.id)).scalars()}
    ev_words = {"PLANNED": "Truck planned", "UPDATED": "Truck plan changed", "LOADED": "Loaded",
                "DISPATCHED": "Sent out", "DELIVERED": "Delivered", "PARTIALLY_DELIVERED": "Delivered (short)",
                "CANCELLED": "Truck cancelled"}
    if dos:
        for e in db.execute(select(DeliveryEvent).where(DeliveryEvent.do_id.in_(dos)).order_by(DeliveryEvent.at)
                            ).scalars():
            d = dos[e.do_id]
            out.append({"at": e.at, "who": who(e.actor_id), "kind": "truck",
                        "what": f"{ev_words.get(e.event, e.event.capitalize())} · {d.do_no}", "detail": e.notes})
        for m in db.execute(select(DealerMessage).where(DealerMessage.kind == "DELIVERY",
                                                        DealerMessage.ref_id.in_(dos))).scalars():
            out.append({"at": m.created_at, "who": None, "kind": "whatsapp",
                        "what": "WhatsApp sent to dealer" if m.status != "FAILED" else "WhatsApp to dealer failed",
                        "detail": m.error if m.status == "FAILED" else f"to {m.phone}"})
    t = db.execute(select(TallySale).where(TallySale.order_id == o.id)).scalar_one_or_none()
    if t is not None:
        if t.pushed_at:
            out.append({"at": t.pushed_at, "who": None, "kind": "tally", "what": "Sent to Tally (waiting approval)",
                        "detail": f"₹{t.amount}" if t.amount else None})
        elif t.status == "FAILED":
            out.append({"at": t.queued_at, "who": None, "kind": "tally", "what": "Could not send to Tally",
                        "detail": t.error})
        if t.decided_at:
            out.append({"at": t.decided_at, "who": None, "kind": "tally",
                        "what": "Approved in Tally" if t.status == "APPROVED" else "Rejected in Tally",
                        "detail": (f"Voucher {t.tally_voucher_no}" if t.tally_voucher_no else t.error)})
    out.sort(key=lambda x: x["at"])
    return out


@router.get("/{order_id}", response_model=OrderOut)
def get(order_id: uuid.UUID, ctx: Ctx = Depends(require("orders.view")), db: Session = Depends(get_db)):
    return order_out(db, ctx, service.get_order(db, ctx, order_id))


@router.put("/{order_id}", response_model=OrderOut)
def update(order_id: uuid.UUID, body: OrderUpdate, ctx: Ctx = Depends(require("orders.create")),
           db: Session = Depends(get_db)):
    order = service.get_order(db, ctx, order_id, lock=True)
    service.update_draft(db, ctx, order, body)
    db.commit()
    return order_out(db, ctx, order)



@router.post("/{order_id}/submit", response_model=OrderOut)
def submit(order_id: uuid.UUID, body: SubmitIn, ctx: Ctx = Depends(require("orders.create")),
           db: Session = Depends(get_db)):
    order = service.get_order(db, ctx, order_id, lock=True)
    service.submit_order(db, ctx, order, body.confirm_duplicate)
    db.commit()
    return order_out(db, ctx, order)


@router.post("/{order_id}/approve", response_model=OrderOut)
def approve(order_id: uuid.UUID, body: ApproveIn, ctx: Ctx = Depends(require("orders.approve")),
            db: Session = Depends(get_db)):
    order = service.get_order(db, ctx, order_id, lock=True)
    service.approve(db, ctx, order, body.reason, body.override_credit)
    db.commit()
    return order_out(db, ctx, order)


@router.post("/{order_id}/hold", response_model=OrderOut)
def hold(order_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("orders.hold")),
         db: Session = Depends(get_db)):
    order = service.get_order(db, ctx, order_id, lock=True)
    service.hold(db, ctx, order, body.reason)
    db.commit()
    return order_out(db, ctx, order)


@router.post("/{order_id}/release", response_model=OrderOut)
def release(order_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("orders.hold")),
            db: Session = Depends(get_db)):
    order = service.get_order(db, ctx, order_id, lock=True)
    service.release(db, ctx, order, body.reason)
    db.commit()
    return order_out(db, ctx, order)


@router.post("/{order_id}/reject", response_model=OrderOut)
def reject(order_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("orders.approve")),
           db: Session = Depends(get_db)):
    order = service.get_order(db, ctx, order_id, lock=True)
    service.reject(db, ctx, order, body.reason)
    db.commit()
    return order_out(db, ctx, order)


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel(order_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("orders.view")),
           db: Session = Depends(get_db)):
    order = service.get_order(db, ctx, order_id, lock=True)
    service.cancel(db, ctx, order, body.reason)  # permission depends on the order's status
    db.commit()
    return order_out(db, ctx, order)


@router.post("/{order_id}/close", response_model=OrderOut)
def close(order_id: uuid.UUID, body: ReasonIn, background: BackgroundTasks,
          ctx: Ctx = Depends(require("orders.close")), db: Session = Depends(get_db)):
    order = service.get_order(db, ctx, order_id, lock=True)
    service.close(db, ctx, order, body.reason)
    db.commit()
    from app.modules.dispatch.router import _push_to_tally

    _push_to_tally(db, ctx, background)
    return order_out(db, ctx, order)
