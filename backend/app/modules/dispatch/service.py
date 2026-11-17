"""Dispatch rules (docs/06 §3). Quantities on a DO are entered in the order line's unit and converted with the
order line's frozen factor, so a DO can never drift from what was sold."""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.deps import Ctx
from app.core.errors import AppError, conflict, forbidden, not_found
from app.core.sequences import format_doc_no, next_number
from app.modules.dispatch.models import DO_ACTIVE, DeliveryEvent, DeliveryOrder, DeliveryOrderItem
from app.modules.masters.models import Company, Driver, Godown, Product, Vehicle
from app.modules.orders import service as orders
from app.modules.orders.models import OPEN_FULFILMENT, SalesOrder, SalesOrderItem
from app.modules.platform.settings import get_setting

ZERO = Decimal("0")
BASE = Decimal("0.000001")
QTY = Decimal("0.001")


def to_base(item: SalesOrderItem, qty: Decimal) -> Decimal:
    return (qty * item.conversion_factor).quantize(BASE, ROUND_HALF_UP)


def to_qty(item: SalesOrderItem, base: Decimal | None) -> Decimal | None:
    if base is None:
        return None
    return (base / item.conversion_factor).quantize(QTY, ROUND_HALF_UP)


def planned_base(db: Session, order_item_ids: list[uuid.UUID], exclude_do: uuid.UUID | None = None) -> dict:
    """Quantity sitting on delivery orders that have not left the gate, per order line."""
    stmt = (
        select(DeliveryOrderItem.order_item_id,
               func.sum(func.coalesce(DeliveryOrderItem.loaded_base, DeliveryOrderItem.planned_base)))
        .join(DeliveryOrder, DeliveryOrder.id == DeliveryOrderItem.do_id)
        .where(DeliveryOrderItem.order_item_id.in_(order_item_ids), DeliveryOrder.status.in_(DO_ACTIVE))
        .group_by(DeliveryOrderItem.order_item_id)
    )
    if exclude_do is not None:
        stmt = stmt.where(DeliveryOrder.id != exclude_do)
    return {k: v for k, v in db.execute(stmt)}


def remaining_base(item: SalesOrderItem, planned: Decimal) -> Decimal:
    return item.base_qty - item.qty_cancelled_base - item.qty_dispatched_base - planned


def _event(db: Session, do: DeliveryOrder, event: str, ctx: Ctx, notes: str | None = None) -> None:
    db.add(DeliveryEvent(do_id=do.id, event=event, notes=notes, actor_id=ctx.user_id, at=orders.now()))
    write_audit(db, action=f"delivery_order.{event.lower()}", entity_type="delivery_orders", entity_id=do.id,
                actor_id=ctx.user_id, company_id=do.company_id, after={"status": do.status, "notes": notes})


def _check_godown_scope(ctx: Ctx, godown_id: uuid.UUID) -> None:
    if ctx.godown_ids is not None and godown_id not in ctx.godown_ids:
        raise forbidden("This godown is not assigned to you", code="SCOPE_FORBIDDEN")


def get_do(db: Session, ctx: Ctx, do_id: uuid.UUID, lock: bool = False) -> DeliveryOrder:
    stmt = select(DeliveryOrder).where(DeliveryOrder.id == do_id, DeliveryOrder.company_id == ctx.company_id)
    if lock:
        stmt = stmt.with_for_update(of=DeliveryOrder)
    do = db.execute(stmt).scalars().first()
    if do is None:
        raise not_found("Delivery order")
    if ctx.godown_ids is not None and do.godown_id not in ctx.godown_ids:
        raise not_found("Delivery order")
    if ctx.has("scope.own_trips_only") and not _is_my_trip(db, ctx, do):
        raise not_found("Delivery order")
    return do


def _is_my_trip(db: Session, ctx: Ctx, do: DeliveryOrder) -> bool:
    if do.driver_id is None:
        return False
    return db.get(Driver, do.driver_id).user_id == ctx.user_id


def _vehicle(db: Session, ctx: Ctx, vehicle_id: uuid.UUID) -> Vehicle:
    v = db.get(Vehicle, vehicle_id)
    if v is None or v.company_id != ctx.company_id:
        raise AppError(422, "VALIDATION_ERROR", "Unknown vehicle")
    if v.status in ("MAINTENANCE", "INACTIVE"):
        raise AppError(422, "VEHICLE_UNAVAILABLE", f"{v.registration_no} is {v.status.lower()}")
    return v


def _driver(db: Session, ctx: Ctx, driver_id: uuid.UUID | None) -> Driver | None:
    if driver_id is None:
        return None
    d = db.get(Driver, driver_id)
    if d is None or d.company_id != ctx.company_id or not d.is_active:
        raise AppError(422, "VALIDATION_ERROR", "Unknown or inactive driver")
    return d


def _check_capacity(db: Session, ctx: Ctx, vehicle: Vehicle, weight: Decimal | None, exclude_do: uuid.UUID | None,
                    override_reason: str | None) -> None:
    if weight is None:
        return  # a product without a weight conversion cannot be capacity-checked; shown as "weight unknown"
    stmt = select(func.coalesce(func.sum(DeliveryOrder.planned_weight_kg), 0)).where(
        DeliveryOrder.vehicle_id == vehicle.id, DeliveryOrder.status.in_(DO_ACTIVE))
    if exclude_do is not None:
        stmt = stmt.where(DeliveryOrder.id != exclude_do)
    already = db.execute(stmt).scalar_one()
    tol = Decimal(str(get_setting(db, ctx.company_id, "dispatch.capacity_tolerance_pct")))
    limit = vehicle.capacity_kg * (Decimal(100) + tol) / Decimal(100)
    total = already + weight
    if total > limit:
        detail = (f"{vehicle.registration_no} would carry {total:,.0f} kg; capacity {vehicle.capacity_kg:,.0f} kg "
                  f"(+{tol}% allowed)")
        if not ctx.has("dispatch.capacity_override"):
            raise AppError(422, "OVER_CAPACITY", detail)
        if not override_reason:
            raise AppError(422, "OVER_CAPACITY", detail + ". Give a reason to load anyway.")


def _lock_items(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, SalesOrderItem]:
    rows = db.execute(select(SalesOrderItem).where(SalesOrderItem.id.in_(ids)).with_for_update()).scalars()
    return {r.id: r for r in rows}


def _line_label(db: Session, oi: SalesOrderItem) -> str:
    return f"Line {oi.line_no} ({db.get(Product, oi.product_id).name})"


def _fmtq(q: Decimal | None) -> str:
    return "0" if q is None else f"{q.normalize():f}"


def grow_line(db: Session, ctx: Ctx, order: SalesOrder, oi: SalesOrderItem, need_base: Decimal, do_no: str) -> None:
    """More went out than the order line has room for: increase the line (quantity and amount, same rate) so that
    `need_base` in total can have gone out. A part closed earlier is reopened first."""
    room = oi.base_qty - oi.qty_cancelled_base
    if need_base <= room:
        return
    extra = need_base - room
    before = to_qty(oi, room)
    back = min(extra, oi.qty_cancelled_base)
    oi.qty_cancelled_base -= back
    grow = extra - back
    if grow > 0:
        old = oi.base_qty
        oi.base_qty = old + grow
        oi.entered_qty = (oi.base_qty / oi.conversion_factor).quantize(QTY, ROUND_HALF_UP)
        oi.rate_qty = (oi.rate_qty * oi.base_qty / old).quantize(BASE, ROUND_HALF_UP)
        oi.amount = (oi.rate_qty * oi.rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
        order.total_amount = sum((i.amount for i in order.items), ZERO)
    oi.status = "OPEN"
    note = (f"{_line_label(db, oi)}: {_fmtq(before)} → {_fmtq(to_qty(oi, need_base))} {oi.entered_uom} "
            f"(more sent on {do_no})")
    orders._history(db, order, order.status, "qty_change", ctx.user_id, note)
    write_audit(db, action="order.qty_change", entity_type="sales_orders", entity_id=order.id, actor_id=ctx.user_id,
                company_id=order.company_id, after={"line_no": oi.line_no, "note": note})


def close_rest(db: Session, ctx: Ctx, order: SalesOrder, do: DeliveryOrder, items: list[SalesOrderItem]) -> None:
    """The dealer will not take the rest of these order lines: close what is not on a truck yet."""
    others = planned_base(db, [oi.id for oi in items], exclude_do=do.id)
    notes = []
    for oi in items:
        rest = remaining_base(oi, others.get(oi.id, ZERO))
        if rest > 0:
            oi.qty_cancelled_base += rest
            notes.append(f"{_line_label(db, oi)}: {_fmtq(to_qty(oi, rest))} {oi.entered_uom}")
    if notes:
        orders._history(db, order, order.status, "rest_closed", ctx.user_id,
                        "Not to be sent (" + do.do_no + "): " + "; ".join(notes))


def _check_truck_extra(db: Session, do: DeliveryOrder, oi: SalesOrderItem, extra: Decimal) -> None:
    """A factory truck can only give what is still on it."""
    from types import SimpleNamespace

    from app.modules.purchases.models import DirectTruck

    if not do.direct_truck_id or extra <= 0:
        return
    truck = db.get(DirectTruck, do.direct_truck_id)
    if truck.supplier == "manual":  # entered by hand for this delivery: it carried what was delivered
        for ti in truck.items:
            if ti.product_id == oi.product_id:
                ti.base_qty = (ti.base_qty or ZERO) + extra
                ti.qty = (ti.qty or ZERO) + (extra / oi.conversion_factor).quantize(QTY, ROUND_HALF_UP)
                return
    _check_truck_load(db, truck, [SimpleNamespace(order_item_id=oi.id, product_id=oi.product_id, planned_base=extra)],
                      {oi.id: oi})


def _direct_truck(db: Session, ctx: Ctx, truck_id: uuid.UUID):
    from app.modules.purchases.models import DirectTruck

    t = db.get(DirectTruck, truck_id)
    if t is None or t.company_id != ctx.company_id:
        raise AppError(422, "VALIDATION_ERROR", "Unknown direct truck")
    if t.status != "OPEN":
        raise AppError(422, "TRUCK_CLOSED", f"Truck {t.truck_no} is already fully delivered or closed")
    return t


def create_do(db: Session, ctx: Ctx, body) -> DeliveryOrder:
    """Plan a delivery order. From a direct (factory) truck it is sent out at once; from an own truck it can be
    sent out at once too (send_now) when the driver is known — otherwise it waits for loading and the gate."""
    order = orders.get_order(db, ctx, body.sales_order_id, lock=True)
    if order.status not in OPEN_FULFILMENT:
        raise conflict("ORDER_NOT_DISPATCHABLE", f"Order is {order.status.replace('_', ' ').lower()}; "
                       "only approved orders can be dispatched")
    adhoc = bool(body.direct_truck_no and body.direct_truck_no.strip()) and not body.direct_truck_id
    if adhoc and body.vehicle_id is None:
        g = db.get(Godown, body.godown_id) if body.godown_id else None
        if g is None or g.company_id != ctx.company_id or g.kind != "DIRECT":
            raise AppError(422, "VALIDATION_ERROR", "A factory truck number can only be given for the Direct godown")
    elif (body.vehicle_id is None) == (body.direct_truck_id is None):
        raise AppError(422, "VALIDATION_ERROR", "Choose either an own truck or a direct truck")
    truck = _direct_truck(db, ctx, body.direct_truck_id) if body.direct_truck_id else None
    godown = db.get(Godown, truck.godown_id if truck else body.godown_id) if (truck or body.godown_id) else None
    if godown is None or godown.company_id != ctx.company_id:
        raise AppError(422, "VALIDATION_ERROR", "Choose the godown")
    if not godown.erp_enabled and truck is None:
        raise AppError(422, "GODOWN_DISABLED", f"Godown {godown.name} is switched off in the ERP")
    _check_godown_scope(ctx, godown.id)
    vehicle = _vehicle(db, ctx, body.vehicle_id) if body.vehicle_id else None
    _driver(db, ctx, body.driver_id)

    by_id = {i.id: i for i in order.items}
    ids = [ln.order_item_id for ln in body.lines]
    if len(set(ids)) != len(ids):
        raise AppError(422, "VALIDATION_ERROR", "Each order line can appear only once on a delivery order")
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise AppError(422, "VALIDATION_ERROR", "Line does not belong to this order")
    locked = _lock_items(db, ids)
    planned = planned_base(db, ids)
    weight, weight_known = ZERO, True
    company = db.get(Company, ctx.company_id)
    do = DeliveryOrder(
        company_id=ctx.company_id, sales_order_id=order.id, dealer_id=order.dealer_id, godown_id=godown.id,
        vehicle_id=vehicle.id if vehicle else None, direct_truck_id=truck.id if truck else None,
        driver_id=body.driver_id, dispatch_date=body.dispatch_date or orders.now().date(),
        status="PLANNED", supplier_ref=body.supplier_ref or (truck.invoice_no if truck else None), remarks=body.remarks,
        capacity_override_reason=body.capacity_override_reason, created_by=ctx.user_id, do_no="",
    )
    for n, ln in enumerate(body.lines, start=1):
        item = locked[ln.order_item_id]
        if item.status != "OPEN":
            raise AppError(422, "LINE_CLOSED", f"Line {item.line_no} is {item.status.lower()}")
        base = to_base(item, ln.qty)
        left = remaining_base(item, planned.get(item.id, ZERO))
        if base > left and not body.allow_more:
            raise AppError(422, "QTY_EXCEEDS_ORDER",
                           f"Line {item.line_no}: only {to_qty(item, max(left, ZERO))} {item.entered_uom} left to dispatch. "
                           "To send more, confirm that the order should be increased.",
                           line_no=item.line_no, left_qty=str(to_qty(item, max(left, ZERO))))
        w = orders.weight_per_base(db, db.get(Product, item.product_id))
        if w is None:
            weight_known = False
        else:
            weight += base * w
        do.items.append(DeliveryOrderItem(line_no=n, order_item_id=item.id, product_id=item.product_id,
                                          uom=item.entered_uom, planned_qty=ln.qty, planned_base=base))
    if adhoc:
        truck = _adhoc_truck(db, ctx, godown, body, do.items, locked)
        do.direct_truck_id = truck.id
        do.supplier_ref = do.supplier_ref or truck.invoice_no
    elif truck:
        _check_truck_load(db, truck, do.items, locked)
    do.planned_weight_kg = weight.quantize(QTY) if weight_known else None
    if vehicle:
        _check_capacity(db, ctx, vehicle, do.planned_weight_kg, None, body.capacity_override_reason)
    do.do_no = format_doc_no(company.doc_prefix, "DO", next_number(db, ctx.company_id, "DO", do.dispatch_date),
                             do.dispatch_date)
    db.add(do)
    db.flush()
    _event(db, do, "PLANNED", ctx, f"{vehicle_label(db, do)}, {len(do.items)} line(s)")
    if truck or body.send_now:
        if not ctx.has("dispatch.confirm"):
            if body.send_now:
                raise forbidden("You can plan the truck but not send it out (needs dispatch.confirm)")
            return do  # direct truck planned; someone with dispatch rights sends it out
        from app.modules.dispatch.schemas import DispatchIn

        dispatch(db, ctx, do, DispatchIn(driver_id=body.driver_id, allow_more=body.allow_more,
                                          close_rest=body.close_rest))
    return do


def _adhoc_truck(db: Session, ctx: Ctx, godown: Godown, body, items: list[DeliveryOrderItem], order_items: dict):
    """A factory truck at the Direct godown that is not in the Direct trucks list (its purchase was entered
    outside the ERP): recorded with exactly what this delivery takes from it."""
    from app.modules.masters.models import CompanyProduct
    from app.modules.purchases.models import DirectTruck, DirectTruckItem

    no = body.direct_truck_no.strip().upper().replace(" ", "")
    t = DirectTruck(company_id=ctx.company_id, invoice_key=f"manual:{uuid.uuid4()}", supplier="manual",
                    invoice_no=(body.supplier_ref or no)[:80], invoice_date=body.dispatch_date or orders.now().date(),
                    truck_no=no, godown_id=godown.id, status="OPEN", created_by=ctx.user_id)
    for it in items:
        oi = order_items[it.order_item_id]
        cp = db.get(CompanyProduct, (ctx.company_id, it.product_id))
        name = (cp.tally_stock_item_name if cp else None) or db.get(Product, it.product_id).name
        t.items.append(DirectTruckItem(tally_item=name, product_id=it.product_id, qty=it.planned_qty,
                                       uom=oi.entered_uom, base_qty=it.planned_base))
    db.add(t)
    db.flush()
    return t


def vehicle_label(db: Session, do: DeliveryOrder) -> str:
    if do.vehicle_id:
        return db.get(Vehicle, do.vehicle_id).registration_no
    from app.modules.purchases.models import DirectTruck

    t = db.get(DirectTruck, do.direct_truck_id)
    return f"{t.truck_no} (direct)"


def _check_truck_load(db: Session, truck, items: list[DeliveryOrderItem], order_items: dict) -> None:
    """A direct truck can only deliver what is still on it."""
    from app.modules.purchases.stock import truck_lines

    left = {}
    for ln in truck_lines(db, [truck])[truck.id]:
        if ln["product_id"]:
            left[ln["product_id"]] = left.get(ln["product_id"], ZERO) + (ln["left_base"] if ln["left_base"] is not None
                                                                         else Decimal("1e12"))
    for it in items:
        oi = order_items[it.order_item_id]
        have = left.get(it.product_id)
        if have is None:
            raise AppError(422, "NOT_ON_TRUCK", f"Line {oi.line_no}: truck {truck.truck_no} does not carry this product",
                           line_no=oi.line_no)
        if it.planned_base > have:
            raise AppError(422, "MORE_THAN_ON_TRUCK",
                           f"Line {oi.line_no}: only {to_qty(oi, max(have, ZERO))} {oi.entered_uom} left on "
                           f"truck {truck.truck_no}", line_no=oi.line_no)
        left[it.product_id] = have - it.planned_base


def update_do(db: Session, ctx: Ctx, do: DeliveryOrder, body) -> None:
    if do.status != "PLANNED":
        raise conflict("INVALID_TRANSITION", "Vehicle and driver can be changed only before loading")
    if body.version != do.version:
        raise conflict("VERSION_CONFLICT", "Someone else changed this delivery order. Reload and try again.")
    _check_godown_scope(ctx, do.godown_id)
    notes = []
    if body.vehicle_id and body.vehicle_id != do.vehicle_id:
        v = _vehicle(db, ctx, body.vehicle_id)
        _check_capacity(db, ctx, v, do.planned_weight_kg, do.id, body.capacity_override_reason)
        do.vehicle_id = v.id
        do.capacity_override_reason = body.capacity_override_reason or do.capacity_override_reason
        notes.append(f"vehicle → {v.registration_no}")
    if "driver_id" in body.model_fields_set:
        d = _driver(db, ctx, body.driver_id)
        do.driver_id = d.id if d else None
        notes.append(f"driver → {d.name if d else 'none'}")
    if body.dispatch_date:
        do.dispatch_date = body.dispatch_date
    if "remarks" in body.model_fields_set:
        do.remarks = body.remarks
    do.version += 1
    do.updated_by = ctx.user_id
    _event(db, do, "UPDATED", ctx, ", ".join(notes) or None)


def _line_qtys(do: DeliveryOrder, lines, field: str) -> dict:
    by_id = {i.id: i for i in do.items}
    out = {}
    for ln in lines:
        if ln.do_item_id not in by_id:
            raise AppError(422, "VALIDATION_ERROR", "Line does not belong to this delivery order")
        out[ln.do_item_id] = ln
    return out


def load(db: Session, ctx: Ctx, do: DeliveryOrder, body) -> None:
    if do.status != "PLANNED":
        raise conflict("INVALID_TRANSITION", f"Delivery order is {do.status.lower()}")
    _check_godown_scope(ctx, do.godown_id)
    given = _line_qtys(do, body.lines, "qty")
    order_items = {i.id: i for i in db.get(SalesOrder, do.sales_order_id).items}
    for it in do.items:
        oi = order_items[it.order_item_id]
        if it.id in given:
            base = to_base(oi, given[it.id].qty)
            if base > it.planned_base:
                raise AppError(422, "QTY_EXCEEDS_PLAN",
                               f"Line {it.line_no}: loaded more than planned ({it.planned_qty} {it.uom}). "
                               "Cancel and re-plan for a bigger load.")
            it.loaded_base, it.actual_weight_kg = base, given[it.id].actual_weight_kg
        else:
            it.loaded_base = it.planned_base
    do.status, do.loaded_at = "LOADED", orders.now()
    do.version += 1
    _event(db, do, "LOADED", ctx, body.remarks)


def dispatch(db: Session, ctx: Ctx, do: DeliveryOrder, body) -> SalesOrder:
    if do.status not in DO_ACTIVE:
        raise conflict("INVALID_TRANSITION", f"Delivery order is {do.status.lower()}")
    _check_godown_scope(ctx, do.godown_id)
    order = db.execute(select(SalesOrder).where(SalesOrder.id == do.sales_order_id).with_for_update()).scalar_one()
    if order.status not in OPEN_FULFILMENT:
        raise conflict("ORDER_NOT_DISPATCHABLE", f"Order {order.order_no} is {order.status.replace('_', ' ').lower()}"
                       f"{': ' + order.hold_reason if order.hold_reason else ''}")
    if body.driver_id:
        do.driver_id = _driver(db, ctx, body.driver_id).id
    if do.driver_id is None and do.vehicle_id is not None:  # a factory truck comes with its own driver
        raise AppError(422, "DRIVER_REQUIRED", "Choose the driver before the truck leaves")
    given = _line_qtys(do, body.lines, "qty")
    locked = _lock_items(db, [it.order_item_id for it in do.items])
    total = ZERO
    for it in do.items:
        oi = locked[it.order_item_id]
        ceiling = it.loaded_base if it.loaded_base is not None else it.planned_base
        base = to_base(oi, given[it.id].qty) if it.id in given else ceiling
        left = oi.base_qty - oi.qty_cancelled_base - oi.qty_dispatched_base
        if base > ceiling and not body.allow_more:
            raise AppError(422, "QTY_EXCEEDS_PLAN", f"Line {it.line_no}: more than loaded/planned "
                           f"({to_qty(oi, ceiling)} {it.uom}). To send more, confirm that the order should be increased.",
                           line_no=it.line_no)
        if base > left and not body.allow_more:
            raise AppError(422, "QTY_EXCEEDS_ORDER", f"Line {it.line_no}: more than is left on the order "
                           f"({to_qty(oi, max(left, ZERO))} {it.uom}). To send more, confirm that the order should be "
                           "increased.", line_no=it.line_no)
        if base > ceiling:
            _check_truck_extra(db, do, oi, base - ceiling)
        if base > left:
            grow_line(db, ctx, order, oi, oi.qty_dispatched_base + base, do.do_no)
        if it.id in given and given[it.id].actual_weight_kg is not None:
            it.actual_weight_kg = given[it.id].actual_weight_kg
        it.dispatched_base = base
        oi.qty_dispatched_base += base
        total += base
    if total == 0:
        raise AppError(422, "NOTHING_TO_DISPATCH", "Nothing is being dispatched. Cancel the delivery order instead.")
    do.status, do.dispatched_at = "DISPATCHED", orders.now()
    do.version += 1
    if do.vehicle_id:
        db.get(Vehicle, do.vehicle_id).status = "ON_TRIP"
    driver = db.get(Driver, do.driver_id) if do.driver_id else None
    _event(db, do, "DISPATCHED", ctx, vehicle_label(db, do) + (f" · {driver.name}" if driver else "") +
           (f" · {body.remarks}" if body.remarks else ""))
    db.flush()
    if body.close_rest:
        close_rest(db, ctx, order, do, list(locked.values()))
        db.flush()
    if do.direct_truck_id:
        _close_truck_if_empty(db, do.direct_truck_id)
    orders.recompute_status(db, order, ctx)
    return order


def _close_truck_if_empty(db: Session, truck_id: uuid.UUID) -> None:
    from app.modules.purchases.models import DirectTruck
    from app.modules.purchases.stock import close_if_empty

    close_if_empty(db, db.get(DirectTruck, truck_id))


def _free_vehicle(db: Session, vehicle_id: uuid.UUID | None) -> None:
    if vehicle_id is None:
        return
    on_road = db.execute(select(func.count()).where(DeliveryOrder.vehicle_id == vehicle_id,
                                                    DeliveryOrder.status == "DISPATCHED")).scalar_one()
    v = db.get(Vehicle, vehicle_id)
    if on_road == 0 and v.status == "ON_TRIP":
        v.status = "AVAILABLE"


def deliver(db: Session, ctx: Ctx, do: DeliveryOrder, body) -> SalesOrder:
    if do.status != "DISPATCHED":
        raise conflict("INVALID_TRANSITION", f"Delivery order is {do.status.lower()}")
    given = _line_qtys(do, body.lines, "delivered_qty")
    order = db.execute(select(SalesOrder).where(SalesOrder.id == do.sales_order_id).with_for_update()).scalar_one()
    locked = _lock_items(db, [it.order_item_id for it in do.items])
    complete = True
    changes = []
    for it in do.items:
        oi = locked[it.order_item_id]
        if it.id in given:
            g = given[it.id]
            d, s, dm = to_base(oi, g.delivered_qty), to_base(oi, g.short_qty), to_base(oi, g.damaged_qty)
            r = to_base(oi, g.returned_qty)
            sent = it.dispatched_base
            total = d + s + dm + r
            if total < sent or (total > sent and (r > 0 or not body.allow_more)):
                raise AppError(422, "POD_MISMATCH",
                               f"Line {it.line_no}: delivered + short + damaged + came back must equal "
                               f"{to_qty(oi, sent)} {it.uom} sent"
                               + (". If the truck carried more, confirm that the order should be increased."
                                  if total > sent else ""), line_no=it.line_no)
            if total > sent:  # the truck carried more than was recorded
                extra = total - sent
                _check_truck_extra(db, do, oi, extra)
                grow_line(db, ctx, order, oi, oi.qty_dispatched_base + extra, do.do_no)
                it.dispatched_base += extra
                oi.qty_dispatched_base += extra
                changes.append(f"{_fmtq(to_qty(oi, extra))} {it.uom} more than sent")
            if r > 0:  # came back: no longer counted as gone out
                it.dispatched_base -= r
                oi.qty_dispatched_base -= r
                changes.append(f"{_fmtq(to_qty(oi, r))} {it.uom} came back")
        else:
            d, s, dm = it.dispatched_base, ZERO, ZERO
        it.delivered_base, it.short_base, it.damaged_base = d, s, dm
        oi.qty_delivered_base += d
        complete = complete and s == 0 and dm == 0
    do.status = "DELIVERED" if complete else "PARTIALLY_DELIVERED"
    do.delivered_at = orders.now()
    do.recipient_name, do.recipient_phone, do.delivery_remarks = body.recipient_name, body.recipient_phone, body.remarks
    do.version += 1
    _event(db, do, do.status, ctx, f"Received by {body.recipient_name}" + "".join(f" · {c}" for c in changes)
           + (f" · {body.remarks}" if body.remarks else ""))
    db.flush()
    if body.close_rest:
        close_rest(db, ctx, order, do, list(locked.values()))
        db.flush()
    _free_vehicle(db, do.vehicle_id)
    orders.recompute_status(db, order, ctx)
    return order


def cancel(db: Session, ctx: Ctx, do: DeliveryOrder, reason: str) -> None:
    """Own trucks: only before they leave the gate. Direct trucks are sent out the moment they are planned, so a
    wrong one can still be cancelled until its delivery is recorded; the quantity goes back to the order."""
    undo_direct = do.direct_truck_id is not None and do.status == "DISPATCHED"
    if do.status not in DO_ACTIVE and not undo_direct:
        raise conflict("INVALID_TRANSITION", "Only delivery orders that have not left the gate can be cancelled")
    _check_godown_scope(ctx, do.godown_id)
    order = None
    if undo_direct:
        order = db.execute(select(SalesOrder).where(SalesOrder.id == do.sales_order_id).with_for_update()).scalar_one()
        locked = _lock_items(db, [it.order_item_id for it in do.items])
        for it in do.items:
            locked[it.order_item_id].qty_dispatched_base -= it.dispatched_base
    do.status, do.cancel_reason = "CANCELLED", reason
    do.version += 1
    _event(db, do, "CANCELLED", ctx, reason)
    if do.direct_truck_id:
        from app.modules.purchases.models import DirectTruck

        t = db.get(DirectTruck, do.direct_truck_id)
        if t.status == "CLOSED" and t.close_note == "Fully delivered":
            t.status, t.close_note = "OPEN", None
    if order is not None:
        db.flush()
        orders.recompute_status(db, order, ctx)


def allowed_actions(db: Session, ctx: Ctx, do: DeliveryOrder) -> list[str]:
    can = []
    if do.status == "PLANNED" and ctx.has("dispatch.plan") and do.vehicle_id:
        can.append("edit")
    if do.status == "PLANNED" and ctx.has("dispatch.load") and do.vehicle_id:
        can.append("load")
    if do.status in DO_ACTIVE and ctx.has("dispatch.confirm"):
        can.append("dispatch")
    if (do.status in DO_ACTIVE or (do.status == "DISPATCHED" and do.direct_truck_id)) and ctx.has("dispatch.plan"):
        can.append("cancel")
    if do.status == "DISPATCHED" and ctx.has("delivery.pod"):
        can.append("deliver")
    return can
