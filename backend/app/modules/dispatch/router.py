import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, require
from app.core.errors import forbidden
from app.core.pagination import Page, PageParams, paginate
from app.modules.dispatch import service
from app.modules.dispatch.models import DO_ACTIVE, DeliveryEvent, DeliveryOrder
from app.modules.dispatch.schemas import (
    CancelIn,
    DeliverIn,
    DispatchIn,
    DispatchOptionsOut,
    DOCreate,
    DOOut,
    DOUpdate,
    LoadIn,
    PendingLineOut,
    VehicleLoadOut,
)
from app.modules.identity.models import User
from app.modules.masters.models import Dealer, DealerAddress, Driver, Godown, Product, Vehicle
from app.modules.masters.service import delivery_location
from app.modules.orders import service as orders
from app.modules.orders.models import OPEN_FULFILMENT, SalesOrder, SalesOrderItem
from app.modules.purchases import stock as stock_svc
from app.modules.purchases.models import DirectTruck

router = APIRouter(tags=["dispatch"])
ZERO = Decimal("0")


def _fmt(q: Decimal | None) -> str:
    return f"{q.normalize():f}" if q is not None else ""


def _address(a: DealerAddress | None) -> str | None:
    if a is None:
        return None
    return ", ".join(x for x in [a.label, a.line1, a.line2, a.area, a.city, a.pincode] if x)


def do_out(db: Session, ctx: Ctx, do: DeliveryOrder, events: bool = False) -> dict:
    order = db.get(SalesOrder, do.sales_order_id)
    order_items = {i.id: i for i in order.items}
    products = {p.id: p.name for p in db.execute(
        select(Product).where(Product.id.in_([i.product_id for i in do.items]))).scalars()}
    vehicle = db.get(Vehicle, do.vehicle_id) if do.vehicle_id else None
    truck = db.get(DirectTruck, do.direct_truck_id) if do.direct_truck_id else None
    godown, dealer = db.get(Godown, do.godown_id), db.get(Dealer, do.dealer_id)
    driver = db.get(Driver, do.driver_id) if do.driver_id else None
    q = service.to_qty
    others = service.planned_base(db, [it.order_item_id for it in do.items], exclude_do=do.id)
    items = []
    for it in do.items:
        oi = order_items[it.order_item_id]
        open_ = service.remaining_base(oi, others.get(oi.id, service.ZERO))
        items.append({
            "id": it.id, "line_no": it.line_no, "order_item_id": it.order_item_id, "product_id": it.product_id,
            "product_name": products.get(it.product_id, ""), "uom": it.uom, "planned_qty": it.planned_qty,
            "loaded_qty": q(oi, it.loaded_base), "dispatched_qty": q(oi, it.dispatched_base),
            "delivered_qty": q(oi, it.delivered_base), "short_qty": q(oi, it.short_base),
            "damaged_qty": q(oi, it.damaged_base), "actual_weight_kg": it.actual_weight_kg,
            "order_open_qty": q(oi, max(open_, service.ZERO)),
        })
    out = {
        "id": do.id, "do_no": do.do_no, "status": do.status, "sales_order_id": order.id, "order_no": order.order_no,
        "dealer_id": dealer.id, "dealer_name": dealer.name, "site_name": order.site_name,
        "delivery_address": delivery_location(db, dealer, order.delivery_address_id)[0],
        "godown_id": godown.id, "godown_name": godown.name, "vehicle_id": vehicle.id if vehicle else None,
        "vehicle_no": vehicle.registration_no if vehicle else truck.truck_no,
        "vehicle_capacity_kg": vehicle.capacity_kg if vehicle else None,
        "direct_truck_id": truck.id if truck else None, "source": "DIRECT" if truck else "OWN",
        "driver_id": driver.id if driver else None, "driver_name": driver.name if driver else None,
        "driver_phone": driver.phone if driver else None, "dispatch_date": do.dispatch_date,
        "supplier_ref": do.supplier_ref, "remarks": do.remarks, "planned_weight_kg": do.planned_weight_kg,
        "capacity_override_reason": do.capacity_override_reason, "loaded_at": do.loaded_at,
        "dispatched_at": do.dispatched_at, "delivered_at": do.delivered_at, "recipient_name": do.recipient_name,
        "recipient_phone": do.recipient_phone, "delivery_remarks": do.delivery_remarks,
        "cancel_reason": do.cancel_reason, "version": do.version,
        "summary": "; ".join(f"{_fmt(i['planned_qty'])} {i['uom']} {i['product_name']}" for i in items),
        "items": items, "can": service.allowed_actions(db, ctx, do), "events": [],
    }
    if events:
        evs = db.execute(select(DeliveryEvent).where(DeliveryEvent.do_id == do.id)
                         .order_by(DeliveryEvent.at, DeliveryEvent.id)).scalars().all()
        users = {u.id: u.full_name for u in db.execute(
            select(User).where(User.id.in_({e.actor_id for e in evs if e.actor_id}))).scalars()} if evs else {}
        out["events"] = [{"event": e.event, "notes": e.notes, "actor_name": users.get(e.actor_id), "at": e.at}
                         for e in evs]
    return out


def _push_to_tally(db: Session, ctx: Ctx, background: BackgroundTasks) -> None:
    """An order that just became fully delivered is waiting in tally_sales: send it now, after the response."""
    from app.modules.integrations.tally import sales as tally_sales
    from app.modules.integrations.tally.models import TallySale

    if db.execute(select(TallySale.id).where(TallySale.company_id == ctx.company_id,
                                             TallySale.status == "PENDING").limit(1)).first():
        background.add_task(tally_sales.push_in_background, ctx.company_id)


@router.get("/dispatch/pending", response_model=list[PendingLineOut])
def pending(godown_id: uuid.UUID | None = None, dealer_id: uuid.UUID | None = None,
            ctx: Ctx = Depends(require("dispatch.view")), db: Session = Depends(get_db)):
    """Approved order lines with quantity still to be planned on a truck — the planning board's left column."""
    stmt = (select(SalesOrderItem, SalesOrder)
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .where(SalesOrder.company_id == ctx.company_id, SalesOrder.status.in_(OPEN_FULFILMENT),
                   SalesOrderItem.status == "OPEN")
            .order_by(SalesOrder.priority.desc(), SalesOrder.approved_at, SalesOrderItem.line_no))
    if dealer_id:
        stmt = stmt.where(SalesOrder.dealer_id == dealer_id)
    if godown_id:
        stmt = stmt.where(SalesOrderItem.godown_id == godown_id)
    if ctx.godown_ids is not None:
        stmt = stmt.where(SalesOrderItem.godown_id.in_(ctx.godown_ids))
    rows = db.execute(stmt).all()
    if not rows:
        return []
    planned = service.planned_base(db, [i.id for i, _ in rows])
    dealers = {d.id: d.name for d in db.execute(select(Dealer).where(Dealer.id.in_({o.dealer_id for _, o in rows}))).scalars()}
    products = {p.id: p for p in db.execute(select(Product).where(Product.id.in_({i.product_id for i, _ in rows}))).scalars()}
    godowns = {g.id: g.name for g in db.execute(select(Godown).where(Godown.id.in_({i.godown_id for i, _ in rows if i.godown_id}))).scalars()}
    weights: dict = {}
    out = []
    for i, o in rows:
        pl = planned.get(i.id, ZERO)
        left = service.remaining_base(i, pl)
        if left <= 0:
            continue
        p = products[i.product_id]
        if p.id not in weights:
            w = orders.weight_per_base(db, p)
            weights[p.id] = (w * i.conversion_factor).quantize(Decimal("0.001")) if w is not None else None
        out.append({
            "order_id": o.id, "order_no": o.order_no, "order_date": o.order_date, "priority": o.priority,
            "requested_delivery_date": o.requested_delivery_date, "dealer_id": o.dealer_id,
            "dealer_name": dealers[o.dealer_id], "site_name": o.site_name, "order_item_id": i.id,
            "product_id": p.id, "product_name": p.name, "godown_id": i.godown_id,
            "godown_name": godowns.get(i.godown_id), "uom": i.entered_uom, "ordered_qty": i.entered_qty,
            "dispatched_qty": service.to_qty(i, i.qty_dispatched_base), "planned_qty": service.to_qty(i, pl),
            "remaining_qty": service.to_qty(i, left), "weight_per_unit_kg": weights[p.id],
        })
    return out


@router.get("/dispatch/vehicles", response_model=list[VehicleLoadOut])
def vehicle_loads(ctx: Ctx = Depends(require("dispatch.view")), db: Session = Depends(get_db)):
    """Each truck with the weight already planned on it, so planners can see what space is left."""
    vehicles = db.execute(select(Vehicle).where(Vehicle.company_id == ctx.company_id, Vehicle.status != "INACTIVE")
                          .order_by(Vehicle.registration_no)).scalars().all()
    agg = {vid: (kg, n) for vid, kg, n in db.execute(
        select(DeliveryOrder.vehicle_id, func.coalesce(func.sum(DeliveryOrder.planned_weight_kg), 0), func.count())
        .where(DeliveryOrder.company_id == ctx.company_id, DeliveryOrder.status.in_(DO_ACTIVE))
        .group_by(DeliveryOrder.vehicle_id))}
    road = dict(db.execute(select(DeliveryOrder.vehicle_id, func.count())
                           .where(DeliveryOrder.company_id == ctx.company_id, DeliveryOrder.status == "DISPATCHED")
                           .group_by(DeliveryOrder.vehicle_id)).all())
    return [{"vehicle_id": v.id, "registration_no": v.registration_no, "capacity_kg": v.capacity_kg,
             "status": v.status, "planned_kg": agg.get(v.id, (ZERO, 0))[0], "planned_dos": agg.get(v.id, (ZERO, 0))[1],
             "on_road_dos": road.get(v.id, 0)} for v in vehicles]


@router.get("/dispatch/factory-truck-numbers", response_model=list[dict])
def factory_truck_numbers(q: str | None = Query(default=None, max_length=40), limit: int = Query(default=12, le=50),
                          ctx: Ctx = Depends(require("dispatch.plan")), db: Session = Depends(get_db)):
    """Factory truck numbers used before (typed in dispatch or on purchase bills), most recent first, so a
    returning truck is picked instead of typed again. `q` matches any part of the number (spaces ignored)."""
    stmt = (select(DirectTruck.truck_no, func.max(DirectTruck.created_at).label("last"), func.count().label("n"))
            .where(DirectTruck.company_id == ctx.company_id, DirectTruck.truck_no != "")
            .group_by(DirectTruck.truck_no))
    if q and q.strip():
        stmt = stmt.where(DirectTruck.truck_no.ilike(f"%{q.strip().upper().replace(' ', '')}%"))
    rows = db.execute(stmt.order_by(func.max(DirectTruck.created_at).desc()).limit(limit)).all()
    return [{"truck_no": r.truck_no, "last_used": r.last.date().isoformat() if r.last else None, "times": r.n}
            for r in rows]


@router.get("/orders/{order_id}/dispatch-options", response_model=DispatchOptionsOut)
def dispatch_options(order_id: uuid.UUID, ctx: Ctx = Depends(require("dispatch.plan")), db: Session = Depends(get_db)):
    """Where this order can be sent from: each active godown with its stock (as per Tally, less what is already
    planned on trucks at the gate) for the order's lines, and the direct factory trucks that carry those products."""
    order = orders.get_order(db, ctx, order_id)
    open_items = [i for i in order.items if i.status == "OPEN"]
    planned = service.planned_base(db, [i.id for i in open_items])
    products = {p.id: p.name for p in db.execute(
        select(Product).where(Product.id.in_({i.product_id for i in open_items} or {None}))).scalars()}
    lines, need = [], {}
    for i in open_items:
        left = service.remaining_base(i, planned.get(i.id, ZERO))
        if left > 0:
            need[i.id] = left
            lines.append({"order_item_id": i.id, "product_id": i.product_id, "product_name": products.get(i.product_id, ""),
                          "uom": i.entered_uom, "remaining_qty": service.to_qty(i, left)})
    items = [i for i in open_items if i.id in need]

    avail = stock_svc.availability(db, ctx.company_id)
    gstmt = select(Godown).where(Godown.company_id == ctx.company_id, Godown.erp_enabled.is_(True),
                                 Godown.status == "ACTIVE", Godown.tally_deleted.is_(False),
                                 Godown.kind.in_(["GODOWN", "SIDING", "DIRECT"]))
    if ctx.godown_ids is not None:
        gstmt = gstmt.where(Godown.id.in_(ctx.godown_ids))
    godowns = []
    for g in db.execute(gstmt).scalars():
        glines, enough, has, total = [], True, False, ZERO
        for i in items:
            a = avail.get((g.id, i.product_id))
            stock_q = service.to_qty(i, a["stock"]) if a else None
            reserved_q = service.to_qty(i, a["reserved"]) if a else ZERO
            avail_q = service.to_qty(i, a["available"]) if a else None
            glines.append({"order_item_id": i.id, "stock_qty": stock_q, "reserved_qty": reserved_q,
                           "available_qty": avail_q})
            if a is None or a["available"] < need[i.id]:
                enough = False
            if a and a["available"] > 0:
                has, total = True, total + a["available"]
        godowns.append(({"godown_id": g.id, "name": g.name, "kind": g.kind, "lines": glines,
                          "enough": enough and bool(items), "has_stock": has}, total))
    # godowns with enough stock, then with some, then Direct (factory trucks), then the empty ones
    godowns.sort(key=lambda x: (not x[0]["enough"], not x[0]["has_stock"] and x[0]["kind"] != "DIRECT",
                                x[0]["kind"] == "DIRECT", -x[1], x[0]["name"].lower()))

    trucks = list(db.execute(select(DirectTruck).where(DirectTruck.company_id == ctx.company_id,
                                                       DirectTruck.status == "OPEN")
                             .order_by(DirectTruck.invoice_date, DirectTruck.created_at)).scalars())
    tl = stock_svc.truck_lines(db, trucks)
    by_product = {i.product_id: i for i in items}
    direct = []
    for t in trucks:
        out_lines, matches = [], False
        for ln in tl[t.id]:
            oi = by_product.get(ln["product_id"]) if ln["product_id"] else None
            if oi is not None and ln["left_base"] is not None:
                left = service.to_qty(oi, max(ln["left_base"], ZERO))
            else:
                left = (ln["qty"] * (ln["left_base"] / ln["base_qty"])).quantize(Decimal("0.001")) \
                    if ln["left_base"] is not None and ln["base_qty"] else ln["qty"]
            if oi is not None and (left or 0) > 0:
                matches = True
            out_lines.append({"tally_item": ln["tally_item"], "product_id": ln["product_id"],
                              "order_item_id": oi.id if oi else None, "qty": ln["qty"], "uom": ln["uom"],
                              "left_qty": left})
        if any((x["left_qty"] or 0) > 0 for x in out_lines):
            direct.append({"id": t.id, "truck_no": t.truck_no, "supplier": t.supplier, "invoice_no": t.invoice_no,
                           "invoice_date": t.invoice_date, "source": t.source, "lines": out_lines, "matches": matches})
    direct.sort(key=lambda x: not x["matches"])

    snap = stock_svc.latest(db, ctx.company_id, ok_only=True)
    last = stock_svc.latest(db, ctx.company_id)
    return {"lines": lines, "stock_as_of": snap.finished_at if snap else None,
            "stock_refreshing": bool(last and last.status == "RUNNING"),
            "stock_error": last.error if last and last.status == "FAILED" else None,
            "godowns": [g for g, _ in godowns], "direct_trucks": direct}


@router.get("/delivery-orders", response_model=Page[DOOut])
def list_dos(params: PageParams = Depends(), status: list[str] | None = Query(None),
             godown_id: uuid.UUID | None = None, vehicle_id: uuid.UUID | None = None,
             sales_order_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
             q: str | None = None, ctx: Ctx = Depends(require("dispatch.view")), db: Session = Depends(get_db)):
    stmt = select(DeliveryOrder).where(DeliveryOrder.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(DeliveryOrder.status.in_(status))
    for col, val in ((DeliveryOrder.godown_id, godown_id), (DeliveryOrder.vehicle_id, vehicle_id),
                     (DeliveryOrder.sales_order_id, sales_order_id)):
        if val:
            stmt = stmt.where(col == val)
    if date_from:
        stmt = stmt.where(DeliveryOrder.dispatch_date >= date_from)
    if date_to:
        stmt = stmt.where(DeliveryOrder.dispatch_date <= date_to)
    if q:
        stmt = stmt.join(Dealer, Dealer.id == DeliveryOrder.dealer_id) \
            .outerjoin(Vehicle, Vehicle.id == DeliveryOrder.vehicle_id) \
            .outerjoin(DirectTruck, DirectTruck.id == DeliveryOrder.direct_truck_id) \
            .where(Dealer.name.ilike(f"%{q}%") | DeliveryOrder.do_no.ilike(f"%{q}%")
                   | Vehicle.registration_no.ilike(f"%{q}%") | DirectTruck.truck_no.ilike(f"%{q}%"))
    if ctx.godown_ids is not None:
        stmt = stmt.where(DeliveryOrder.godown_id.in_(ctx.godown_ids))
    if ctx.has("scope.own_trips_only"):
        stmt = stmt.where(DeliveryOrder.driver_id.in_(select(Driver.id).where(Driver.user_id == ctx.user_id)))
    page = paginate(db, stmt, params, {"dispatch_date": DeliveryOrder.dispatch_date, "created_at": DeliveryOrder.created_at},
                    [DeliveryOrder.dispatch_date.desc(), DeliveryOrder.created_at.desc()])
    return {**page, "items": [do_out(db, ctx, d) for d in page["items"]]}


def _view_ctx(ctx: Ctx) -> Ctx:
    if not (ctx.has("dispatch.view") or ctx.has("delivery.update_status")):
        raise forbidden("Missing permission: dispatch.view")
    return ctx


@router.get("/delivery-orders/{do_id}", response_model=DOOut)
def get_do(do_id: uuid.UUID, ctx: Ctx = Depends(require()), db: Session = Depends(get_db)):
    _view_ctx(ctx)
    return do_out(db, ctx, service.get_do(db, ctx, do_id), events=True)


@router.post("/delivery-orders", response_model=DOOut, status_code=201)
def create_do(body: DOCreate, ctx: Ctx = Depends(require("dispatch.plan")), db: Session = Depends(get_db)):
    do = service.create_do(db, ctx, body)
    db.commit()
    return do_out(db, ctx, do, events=True)


@router.patch("/delivery-orders/{do_id}", response_model=DOOut)
def update_do(do_id: uuid.UUID, body: DOUpdate, ctx: Ctx = Depends(require("dispatch.plan")),
              db: Session = Depends(get_db)):
    do = service.get_do(db, ctx, do_id, lock=True)
    service.update_do(db, ctx, do, body)
    db.commit()
    return do_out(db, ctx, do, events=True)


@router.post("/delivery-orders/{do_id}/load", response_model=DOOut)
def load(do_id: uuid.UUID, body: LoadIn, ctx: Ctx = Depends(require("dispatch.load")), db: Session = Depends(get_db)):
    do = service.get_do(db, ctx, do_id, lock=True)
    service.load(db, ctx, do, body)
    db.commit()
    return do_out(db, ctx, do, events=True)


@router.post("/delivery-orders/{do_id}/dispatch", response_model=DOOut)
def dispatch(do_id: uuid.UUID, body: DispatchIn, ctx: Ctx = Depends(require("dispatch.confirm")),
             db: Session = Depends(get_db)):
    do = service.get_do(db, ctx, do_id, lock=True)
    service.dispatch(db, ctx, do, body)
    db.commit()
    return do_out(db, ctx, do, events=True)


@router.post("/delivery-orders/{do_id}/deliver", response_model=DOOut)
def deliver(do_id: uuid.UUID, body: DeliverIn, background: BackgroundTasks,
            ctx: Ctx = Depends(require("delivery.pod")), db: Session = Depends(get_db)):
    do = service.get_do(db, ctx, do_id, lock=True)
    service.deliver(db, ctx, do, body)
    db.commit()
    _push_to_tally(db, ctx, background)
    if do.status == "DELIVERED":
        from app.modules.collections import delivery_msg

        background.add_task(delivery_msg.send_in_background, do.id)
    return do_out(db, ctx, do, events=True)


@router.post("/delivery-orders/{do_id}/cancel", response_model=DOOut)
def cancel(do_id: uuid.UUID, body: CancelIn, ctx: Ctx = Depends(require("dispatch.plan")),
           db: Session = Depends(get_db)):
    do = service.get_do(db, ctx, do_id, lock=True)
    service.cancel(db, ctx, do, body.reason)
    db.commit()
    return do_out(db, ctx, do, events=True)
