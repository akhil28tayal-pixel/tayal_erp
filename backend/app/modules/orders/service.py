"""Order rules (docs/06 §1). Every command: permission + scope → lock → state check → validate → write order,
history and audit in one transaction. Callers commit."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.deps import Ctx
from app.core.errors import AppError, conflict, forbidden, not_found
from app.core.sequences import format_doc_no, next_number
from app.modules.masters.conversions import convert
from app.modules.masters.models import (
    Company,
    CompanyProduct,
    Dealer,
    DealerAddress,
    DealerAssignment,
    Godown,
    Product,
)
from app.modules.masters.service import assigned_dealer_ids_subquery, effective_credit_limit, resolve_rate
from app.modules.orders.models import (
    OPEN_FULFILMENT,
    OrderCreditCheck,
    OrderStatusHistory,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.orders.schemas import OrderIn, OrderLineIn
from app.modules.platform.settings import get_setting

CENT = Decimal("0.01")
BASE = Decimal("0.000001")
ZERO = Decimal("0")


def now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ visibility
def dealer_visible(db: Session, ctx: Ctx, dealer_id: uuid.UUID) -> Dealer:
    dealer = db.get(Dealer, dealer_id)
    if dealer is None or dealer.company_id != ctx.company_id:
        raise not_found("Dealer")
    if not ctx.has("dealers.view_all"):
        assigned = db.execute(
            assigned_dealer_ids_subquery(ctx.user.employee_id).where(DealerAssignment.dealer_id == dealer_id)
        ).first()
        if assigned is None:
            raise not_found("Dealer")  # not assigned to you → behaves as unknown
    return dealer


def orders_visible(stmt, ctx: Ctx):
    """Sales/collection staff see orders of their assigned dealers and orders they booked themselves."""
    if ctx.has("dealers.view_all"):
        return stmt
    return stmt.where(
        SalesOrder.dealer_id.in_(assigned_dealer_ids_subquery(ctx.user.employee_id))
        | (SalesOrder.created_by == ctx.user_id)
    )


def get_order(db: Session, ctx: Ctx, order_id: uuid.UUID, lock: bool = False) -> SalesOrder:
    stmt = orders_visible(select(SalesOrder).where(SalesOrder.id == order_id,
                                                   SalesOrder.company_id == ctx.company_id), ctx)
    if lock:
        stmt = stmt.with_for_update(of=SalesOrder)
    order = db.execute(stmt).scalars().first()
    if order is None:
        raise not_found("Order")
    return order


# ------------------------------------------------------------------ pricing
@dataclass
class PricedLine:
    line_no: int
    product: Product
    godown_id: uuid.UUID | None
    qty: Decimal
    uom: str
    base_qty: Decimal
    factor: Decimal
    rate: Decimal
    rate_uom: str
    rate_qty: Decimal
    list_rate: Decimal | None
    min_rate: Decimal | None
    rate_source: str
    amount: Decimal
    weight_kg: Decimal | None
    remarks: str | None
    flags: list[str] = field(default_factory=list)


def weight_per_base(db: Session, product: Product) -> Decimal | None:
    """Kilograms in one base unit (a bag, a kg, a piece) — None when the product has no weight conversion."""
    if product.base_uom == "KG":
        return Decimal(1)
    try:
        return convert(db, product, Decimal(1), product.base_uom, "KG").result
    except AppError:
        return None


def _sellable_product(db: Session, company_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    cp = db.execute(
        select(CompanyProduct).where(CompanyProduct.company_id == company_id, CompanyProduct.product_id == product_id)
    ).scalar_one_or_none()
    if cp is None:
        raise AppError(422, "PRODUCT_NOT_SELLABLE", "Unknown product for this company")
    p = cp.product
    if not (cp.is_active and cp.erp_enabled and not cp.tally_deleted and p.is_active):
        raise AppError(422, "PRODUCT_NOT_SELLABLE", f"{p.name} is switched off in the ERP")
    return p


def _check_godown(db: Session, company_id: uuid.UUID, godown_id: uuid.UUID | None) -> None:
    if godown_id is None:
        return
    g = db.get(Godown, godown_id)
    if g is None or g.company_id != company_id:
        raise AppError(422, "VALIDATION_ERROR", "Unknown godown")
    if not g.erp_enabled or g.status != "ACTIVE":
        raise AppError(422, "GODOWN_DISABLED", f"Godown {g.name} is switched off in the ERP")


def price_line(db: Session, company_id: uuid.UUID, dealer: Dealer, line_no: int, ln: OrderLineIn,
               on: date) -> PricedLine:
    p = _sellable_product(db, company_id, ln.product_id)
    _check_godown(db, company_id, ln.godown_id)
    base = convert(db, p, ln.qty, ln.uom, p.base_uom, on)
    resolved = resolve_rate(db, company_id, dealer.id, p.id, ln.godown_id, on)
    flags: list[str] = []
    if ln.rate is not None:
        rate = ln.rate
        rate_uom = ln.rate_uom or (resolved["uom"] if resolved else ln.uom)
        source = "MANUAL"
    elif resolved is not None:
        rate, rate_uom, source = resolved["rate"], resolved["uom"], resolved["source"]
    else:
        raise AppError(422, "RATE_REQUIRED", f"No rate is set for this dealer and {p.name}. Enter a rate.",
                       line_no=line_no)
    list_rate = min_rate = None
    if resolved is not None:
        list_rate, min_rate = resolved["rate"], resolved["min_rate"]
        if resolved["uom"] != rate_uom:  # compare like with like: express the dealer rate per rate_uom
            per = convert(db, p, Decimal(1), rate_uom, resolved["uom"], on).result
            list_rate = (list_rate * per).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            min_rate = (min_rate * per).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        if rate < min_rate:
            flags.append("RATE_BELOW_MIN")
    else:
        flags.append("NO_DEALER_RATE")
    rate_qty = ln.qty if rate_uom == ln.uom else convert(db, p, ln.qty, ln.uom, rate_uom, on).result
    amount = (rate_qty * rate).quantize(CENT, ROUND_HALF_UP)
    w = weight_per_base(db, p)
    return PricedLine(
        line_no=line_no, product=p, godown_id=ln.godown_id, qty=ln.qty, uom=ln.uom, base_qty=base.result,
        factor=base.factor, rate=rate, rate_uom=rate_uom, rate_qty=rate_qty, list_rate=list_rate,
        min_rate=min_rate, rate_source=source, amount=amount,
        weight_kg=(base.result * w).quantize(Decimal("0.001")) if w is not None else None,
        remarks=ln.remarks, flags=flags,
    )


def price_order(db: Session, ctx: Ctx, body: OrderIn) -> tuple[Dealer, list[PricedLine]]:
    dealer = dealer_visible(db, ctx, body.dealer_id)
    if dealer.status != "ACTIVE":
        reason = f": {dealer.blocked_reason}" if dealer.blocked_reason else ""
        raise AppError(422, "DEALER_BLOCKED", f"{dealer.name} is {dealer.status.lower()}{reason}")
    if body.delivery_address_id is not None:
        addr = db.get(DealerAddress, body.delivery_address_id)
        if addr is None or addr.dealer_id != dealer.id:
            raise AppError(422, "VALIDATION_ERROR", "Delivery address does not belong to this dealer")
    on = body.order_date or date.today()
    return dealer, [price_line(db, ctx.company_id, dealer, i + 1, ln, on) for i, ln in enumerate(body.lines)]


# ------------------------------------------------------------------ credit
def open_order_exposure(db: Session, dealer_id: uuid.UUID, exclude_order_id: uuid.UUID | None) -> Decimal:
    """Value of approved orders not yet dispatched. Dispatched material is billed in Tally, so it shows up in
    the Tally balance after the next sync instead."""
    stmt = (
        select(SalesOrderItem.amount, SalesOrderItem.base_qty, SalesOrderItem.qty_cancelled_base,
               SalesOrderItem.qty_dispatched_base)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .where(SalesOrder.dealer_id == dealer_id, SalesOrder.status.in_(OPEN_FULFILMENT),
               SalesOrderItem.status == "OPEN")
    )
    if exclude_order_id is not None:
        stmt = stmt.where(SalesOrder.id != exclude_order_id)
    total = ZERO
    for amount, base, cancelled, dispatched in db.execute(stmt):
        open_base = base - cancelled - dispatched
        if open_base > 0:
            total += amount * open_base / base
    return total.quantize(CENT, ROUND_HALF_UP)


def credit_position(db: Session, company_id: uuid.UUID, dealer: Dealer, amount: Decimal,
                    exclude_order_id: uuid.UUID | None = None) -> dict:
    limit = effective_credit_limit(dealer, get_setting(db, company_id, "credit.limit_source"))
    exposure = open_order_exposure(db, dealer.id, exclude_order_id)
    threshold = timedelta(minutes=int(get_setting(db, company_id, "tally.stale_threshold_minutes")))
    stale = dealer.tally_balance_at is None or now() - dealer.tally_balance_at > threshold
    balance = dealer.tally_balance
    reasons: list[dict] = []
    result = "PASS"
    available = None
    if limit is None:
        if get_setting(db, company_id, "credit.no_limit_policy") == "HOLD":
            result = "HOLD"
            reasons.append({"code": "NO_CREDIT_LIMIT", "detail": "No credit limit is set for this dealer"})
        else:
            reasons.append({"code": "NO_CREDIT_LIMIT", "detail": "No credit limit set — credit not checked"})
    else:
        available = (limit - (balance or ZERO) - exposure).quantize(CENT)
        if stale:
            when = dealer.tally_balance_at.astimezone().strftime("%d %b %H:%M") if dealer.tally_balance_at else "never"
            policy = get_setting(db, company_id, "credit.stale_policy")
            detail = f"Tally balance is not current (last synced: {when})"
            if policy == "HOLD":
                result = "HOLD"
                reasons.append({"code": "STALE_BALANCE", "detail": detail})
            elif policy == "WARN":
                result = "WARN"
                reasons.append({"code": "STALE_BALANCE", "detail": detail})
        if amount > available:
            detail = f"Order ₹{amount:,} exceeds available credit ₹{available:,} (limit ₹{limit:,})"
            if dealer.credit_policy == "STRICT":
                result = "HOLD"
            elif result == "PASS":
                result = "WARN"
            reasons.append({"code": "OVER_LIMIT", "detail": detail})
    return {"result": result, "credit_limit": limit, "tally_balance": balance,
            "balance_as_of": dealer.tally_balance_at, "is_stale": stale, "open_order_exposure": exposure,
            "this_order_amount": amount, "available_credit": available, "reasons": reasons}


def record_credit_check(db: Session, order: SalesOrder, dealer: Dealer, stage: str) -> dict:
    pos = credit_position(db, order.company_id, dealer, order.total_amount, exclude_order_id=order.id)
    db.add(OrderCreditCheck(order_id=order.id, checked_at=now(), stage=stage, **pos))
    return pos


def hold_kind_for(pos: dict) -> str:
    codes = {r["code"] for r in pos["reasons"]}
    return "CREDIT" if "OVER_LIMIT" in codes or "NO_CREDIT_LIMIT" in codes else "STALE_BALANCE"


# ------------------------------------------------------------------ writing
def _history(db: Session, order: SalesOrder, to: str, action: str, actor: uuid.UUID | None,
             reason: str | None = None) -> None:
    db.add(OrderStatusHistory(order_id=order.id, from_status=order.status if order.status != to else None,
                              to_status=to, action=action, reason=reason, actor_id=actor, at=now()))


def _set_status(db: Session, order: SalesOrder, to: str, action: str, ctx: Ctx | None,
                reason: str | None = None) -> None:
    actor = ctx.user_id if ctx else None
    db.add(OrderStatusHistory(order_id=order.id, from_status=order.status, to_status=to, action=action,
                              reason=reason, actor_id=actor, at=now()))
    write_audit(db, action=f"order.{action}", entity_type="sales_orders", entity_id=order.id, actor_id=actor,
                company_id=order.company_id, before={"status": order.status},
                after={"status": to, "reason": reason})
    order.status = to
    order.version += 1
    if ctx:
        order.updated_by = ctx.user_id
    from app.modules.platform import notify

    notify.order_status(db, order, to, actor, reason)
    if to == "DELIVERED" or to == "CLOSED":  # what was delivered goes to Tally for the admin's approval
        from app.modules.integrations.tally import sales as tally_sales

        tally_sales.queue(db, order)


def _sales_exec_for(db: Session, dealer: Dealer, ctx: Ctx) -> uuid.UUID | None:
    assigned = db.execute(
        select(DealerAssignment.employee_id).where(DealerAssignment.dealer_id == dealer.id,
                                                   DealerAssignment.kind == "SALES",
                                                   DealerAssignment.valid_to.is_(None))
    ).scalar_one_or_none()
    return assigned or dealer.default_sales_exec_id or ctx.user.employee_id


def write_lines(order: SalesOrder, lines: list[PricedLine]) -> None:
    order.items.clear()
    for pl in lines:
        order.items.append(SalesOrderItem(
            line_no=pl.line_no, product_id=pl.product.id, godown_id=pl.godown_id, entered_qty=pl.qty,
            entered_uom=pl.uom, base_qty=pl.base_qty, base_uom=pl.product.base_uom, conversion_factor=pl.factor,
            rate=pl.rate, rate_uom=pl.rate_uom, rate_qty=pl.rate_qty, list_rate=pl.list_rate, min_rate=pl.min_rate,
            rate_source=pl.rate_source, amount=pl.amount, remarks=pl.remarks,
        ))
    order.total_amount = sum((pl.amount for pl in lines), ZERO)


def _dedupe_hash(order: SalesOrder) -> str:
    lines = sorted((str(i.product_id), str(i.base_qty.normalize()), str(i.godown_id)) for i in order.items)
    raw = json.dumps([str(order.dealer_id), str(order.delivery_address_id), lines])
    return hashlib.sha256(raw.encode()).hexdigest()


def create_order(db: Session, ctx: Ctx, body, submit: bool, confirm_duplicate: bool) -> SalesOrder:
    dealer, lines = price_order(db, ctx, body)
    order = SalesOrder(
        company_id=ctx.company_id, order_date=body.order_date or date.today(), dealer_id=dealer.id,
        delivery_address_id=body.delivery_address_id, site_name=body.site_name,
        requested_delivery_date=body.requested_delivery_date, priority=body.priority, source=body.source,
        special_instructions=body.special_instructions, sales_exec_id=_sales_exec_for(db, dealer, ctx),
        status="DRAFT", created_by=ctx.user_id, review_reasons=[],
    )
    write_lines(order, lines)
    db.add(order)
    db.flush()
    _history(db, order, "DRAFT", "create", ctx.user_id)
    write_audit(db, action="order.create", entity_type="sales_orders", entity_id=order.id, actor_id=ctx.user_id,
                company_id=ctx.company_id, after={"dealer_id": str(dealer.id), "total": str(order.total_amount)})
    if submit:
        submit_order(db, ctx, order, confirm_duplicate)
    return order


def update_draft(db: Session, ctx: Ctx, order: SalesOrder, body) -> SalesOrder:
    if order.status != "DRAFT":
        raise conflict("INVALID_TRANSITION", "Only draft orders can be edited")
    if body.version != order.version:
        raise conflict("VERSION_CONFLICT", "Someone else changed this order. Reload and try again.")
    dealer, lines = price_order(db, ctx, body)
    order.items.clear()
    db.flush()  # old lines go before new ones reuse their line numbers
    for k in ("order_date", "delivery_address_id", "site_name", "requested_delivery_date", "priority", "source",
              "special_instructions"):
        setattr(order, k, getattr(body, k) if k != "order_date" else (body.order_date or order.order_date))
    order.dealer_id = dealer.id
    write_lines(order, lines)
    order.version += 1
    order.updated_by = ctx.user_id
    db.flush()
    if body.submit:
        submit_order(db, ctx, order, body.confirm_duplicate)
    return order


def submit_order(db: Session, ctx: Ctx, order: SalesOrder, confirm_duplicate: bool = False) -> None:
    if order.status != "DRAFT":
        raise conflict("INVALID_TRANSITION", f"Order is {order.status}, not a draft")
    dealer = db.get(Dealer, order.dealer_id)
    if dealer.status != "ACTIVE":
        raise AppError(422, "DEALER_BLOCKED", f"{dealer.name} is {dealer.status.lower()}")
    for item in order.items:  # products may have been switched off since the draft was saved
        _sellable_product(db, order.company_id, item.product_id)
    order.dedupe_hash = _dedupe_hash(order)
    window = now() - timedelta(minutes=int(get_setting(db, order.company_id, "orders.duplicate_window_minutes")))
    dup = db.execute(
        select(SalesOrder.order_no).where(
            SalesOrder.company_id == order.company_id, SalesOrder.dedupe_hash == order.dedupe_hash,
            SalesOrder.id != order.id, SalesOrder.submitted_at >= window,
            SalesOrder.status.notin_(["CANCELLED", "REJECTED", "DRAFT"]))
    ).scalars().first()
    if dup and not confirm_duplicate:
        raise conflict("DUPLICATE_ORDER", f"The same order was booked a few minutes ago as {dup}. "
                       "Submit again with confirm_duplicate=true if this is a second order.", order_no=dup)

    company = db.get(Company, order.company_id)
    order.order_no = format_doc_no(company.doc_prefix, "SO", next_number(db, order.company_id, "SO", order.order_date),
                                   order.order_date)
    order.submitted_at = now()
    reasons = []
    for i in order.items:
        if i.min_rate is not None and i.rate < i.min_rate:
            reasons.append({"code": "RATE_BELOW_MIN", "line_no": i.line_no,
                            "detail": f"Line {i.line_no}: rate {i.rate} is below the allowed {i.min_rate}"})
        elif i.list_rate is None:
            reasons.append({"code": "NO_DEALER_RATE", "line_no": i.line_no,
                            "detail": f"Line {i.line_no}: no dealer rate on file, rate entered by hand"})
    pos = record_credit_check(db, order, dealer, "SUBMIT")
    reasons += [r for r in pos["reasons"] if pos["result"] != "PASS"]
    order.review_reasons = reasons
    if pos["result"] == "HOLD":
        order.hold_kind = hold_kind_for(pos)
        order.hold_reason = "; ".join(r["detail"] for r in pos["reasons"])
        _set_status(db, order, "ON_HOLD", "submit", ctx, reason=order.hold_reason)
    elif reasons or not get_setting(db, order.company_id, "orders.auto_approve"):
        _set_status(db, order, "UNDER_REVIEW", "submit", ctx)
    else:
        order.approved_at, order.approved_by = now(), None
        _set_status(db, order, "APPROVED", "auto_approve", ctx)


def _n(v: Decimal) -> str:
    return f"{v.normalize():f}"


def edit_at_approval(db: Session, ctx: Ctx, order: SalesOrder, changes) -> None:
    """Super admin: change quantities and rates of an order waiting for approval (before it is approved). The
    amounts, the order total and the credit check use the new figures; the change is kept in the order history."""
    if not ctx.has("orders.edit_on_approval"):
        raise forbidden("Only a super admin can change the order while approving", code="EDIT_NOT_ALLOWED")
    if order.status not in ("UNDER_REVIEW", "ON_HOLD"):
        raise conflict("INVALID_TRANSITION", f"Order is {order.status}; only orders waiting for approval can be changed")
    items = {i.line_no: i for i in order.items}
    want = {c.line_no: c for c in changes}
    unknown = sorted(set(want) - set(items))
    if unknown:
        raise AppError(422, "VALIDATION_ERROR", f"Line {unknown[0]} is not on this order")
    dealer = db.get(Dealer, order.dealer_id)
    notes, lines, kept = [], [], {}
    for no in sorted(items):
        i, c = items[no], want.get(no)
        qty = c.qty if c and c.qty is not None else i.entered_qty
        rate = c.rate if c and c.rate is not None else i.rate
        bits = []
        if qty != i.entered_qty:
            bits.append(f"qty {_n(i.entered_qty)} → {_n(qty)} {i.entered_uom.lower()}")
        if rate != i.rate:
            bits.append(f"rate ₹{_n(i.rate)} → ₹{_n(rate)} /{i.rate_uom.lower()}")
        if bits:
            product = db.get(Product, i.product_id)
            notes.append(f"Line {no} {product.name}: " + ", ".join(bits))
        else:
            kept[no] = i.rate_source
        lines.append((no, OrderLineIn(product_id=i.product_id, qty=qty, uom=i.entered_uom, godown_id=i.godown_id,
                                      rate=rate, rate_uom=i.rate_uom, remarks=i.remarks)))
    if not notes:
        return
    before = str(order.total_amount)
    priced = [price_line(db, order.company_id, dealer, no, ln, order.order_date) for no, ln in lines]
    for pl in priced:
        pl.rate_source = kept.get(pl.line_no, "APPROVER")
    order.items.clear()
    db.flush()  # old lines go before new ones reuse their line numbers
    write_lines(order, priced)
    order.version += 1
    order.updated_by = ctx.user_id
    db.flush()
    note = "; ".join(notes) + f". Total ₹{before} → ₹{order.total_amount}"
    _history(db, order, order.status, "edit_on_approval", ctx.user_id, note)
    write_audit(db, action="order.edit_on_approval", entity_type="sales_orders", entity_id=order.id,
                actor_id=ctx.user_id, company_id=order.company_id, after={"changes": notes,
                                                                         "total": str(order.total_amount)})


def approve(db: Session, ctx: Ctx, order: SalesOrder, reason: str | None, override_credit: bool) -> None:
    if order.status not in ("UNDER_REVIEW", "ON_HOLD"):
        raise conflict("INVALID_TRANSITION", f"Order is {order.status}; only orders waiting for approval can be approved")
    if order.created_by == ctx.user_id and not ctx.has("orders.self_approval"):
        raise forbidden("You cannot approve an order you booked yourself", code="SELF_APPROVAL")
    if order.status == "ON_HOLD" and order.hold_kind == "MANUAL":
        raise conflict("INVALID_TRANSITION", "Release the hold first")
    if any(r["code"] == "RATE_BELOW_MIN" for r in order.review_reasons) and not ctx.has("orders.rate_override"):
        raise forbidden("A rate is below the dealer's allowed rate. Only an admin can approve it.",
                        code="RATE_APPROVAL_REQUIRED")
    dealer = db.get(Dealer, order.dealer_id)
    if dealer.status != "ACTIVE":
        raise AppError(422, "DEALER_BLOCKED", f"{dealer.name} is {dealer.status.lower()}")
    if order.status == "ON_HOLD" and not ctx.has("credit.override"):
        raise forbidden("Orders on credit hold are approved by an admin", code="ADMIN_APPROVAL_REQUIRED")
    pos = record_credit_check(db, order, dealer, "APPROVE")
    if pos["result"] == "HOLD":
        if not (ctx.has("credit.override") and override_credit):
            if order.status != "ON_HOLD":
                order.hold_kind = hold_kind_for(pos)
                order.hold_reason = "; ".join(r["detail"] for r in pos["reasons"])
                _set_status(db, order, "ON_HOLD", "credit_hold", ctx, reason=order.hold_reason)
                db.commit()
            raise AppError(409, "CREDIT_HOLD", "Credit check failed. The order is on hold until an admin "
                           "approves it with a credit override.", reasons=pos["reasons"])
        if not reason:
            raise AppError(422, "REASON_REQUIRED", "Give a reason for the credit override")
        write_audit(db, action="order.credit_override", entity_type="sales_orders", entity_id=order.id,
                    actor_id=ctx.user_id, company_id=order.company_id, after={"reason": reason, **{
                        k: str(v) for k, v in pos.items() if k in ("credit_limit", "available_credit")}})
    order.approved_at, order.approved_by = now(), ctx.user_id
    order.hold_kind = order.hold_reason = None
    _set_status(db, order, "APPROVED", "approve", ctx, reason=reason)


def hold(db: Session, ctx: Ctx, order: SalesOrder, reason: str) -> None:
    if order.status not in ("UNDER_REVIEW", "APPROVED", "PARTIALLY_DISPATCHED"):
        raise conflict("INVALID_TRANSITION", f"An order that is {order.status} cannot be put on hold")
    order.hold_kind, order.hold_reason = "MANUAL", reason
    _set_status(db, order, "ON_HOLD", "hold", ctx, reason=reason)


def release(db: Session, ctx: Ctx, order: SalesOrder, reason: str) -> None:
    if order.status != "ON_HOLD" or order.hold_kind != "MANUAL":
        raise conflict("INVALID_TRANSITION", "Credit holds are cleared by an admin approving the order")
    order.hold_kind = order.hold_reason = None
    _set_status(db, order, "UNDER_REVIEW", "release", ctx, reason=reason)


def reject(db: Session, ctx: Ctx, order: SalesOrder, reason: str) -> None:
    if order.status not in ("UNDER_REVIEW", "ON_HOLD"):
        raise conflict("INVALID_TRANSITION", f"An order that is {order.status} cannot be rejected")
    if _dispatched(order) > 0:
        raise conflict("INVALID_TRANSITION", "Material has already gone out on this order")
    _set_status(db, order, "REJECTED", "reject", ctx, reason=reason)


def _dispatched(order: SalesOrder) -> Decimal:
    return sum((i.qty_dispatched_base for i in order.items), ZERO)


def active_do_count(db: Session, order_id: uuid.UUID, statuses: list[str]) -> int:
    from app.modules.dispatch.models import DeliveryOrder

    return db.execute(select(func.count()).where(DeliveryOrder.sales_order_id == order_id,
                                                 DeliveryOrder.status.in_(statuses))).scalar_one()


def cancel(db: Session, ctx: Ctx, order: SalesOrder, reason: str) -> None:
    if order.status == "DRAFT":
        if not (ctx.has("orders.cancel") or order.created_by == ctx.user_id):
            raise forbidden("Missing permission: orders.cancel")
    elif order.status in ("UNDER_REVIEW", "ON_HOLD"):
        if not ctx.has("orders.cancel"):
            raise forbidden("Missing permission: orders.cancel")
    elif order.status == "APPROVED":
        if not ctx.has("orders.cancel_approved"):
            raise forbidden("Missing permission: orders.cancel_approved")
    else:
        raise conflict("INVALID_TRANSITION", f"An order that is {order.status} cannot be cancelled. "
                       "Close it instead to drop the undelivered remainder.")
    if _dispatched(order) > 0 or active_do_count(db, order.id, ["PLANNED", "LOADED"]):
        raise conflict("ORDER_HAS_DISPATCH", "Cancel its delivery orders first")
    for i in order.items:
        i.qty_cancelled_base, i.status = i.base_qty, "CANCELLED"
    order.cancel_reason = reason
    _set_status(db, order, "CANCELLED", "cancel", ctx, reason=reason)


def close(db: Session, ctx: Ctx, order: SalesOrder, reason: str) -> None:
    """Close a part-supplied order: whatever has not gone out is cancelled."""
    if order.status not in OPEN_FULFILMENT + ["DELIVERED"]:
        raise conflict("INVALID_TRANSITION", f"An order that is {order.status} cannot be closed")
    if active_do_count(db, order.id, ["PLANNED", "LOADED", "DISPATCHED"]):
        raise conflict("ORDER_HAS_DISPATCH", "Finish or cancel its open delivery orders first")
    for i in order.items:
        i.qty_cancelled_base = i.base_qty - i.qty_dispatched_base
        i.status = "FULFILLED" if i.qty_dispatched_base > 0 else "CANCELLED"
    order.closed_at = now()
    _set_status(db, order, "CLOSED", "close", ctx, reason=reason)


def recompute_status(db: Session, order: SalesOrder, ctx: Ctx | None) -> None:
    """Fulfilment status follows the quantities (docs/06 §1, derived states)."""
    if order.status not in OPEN_FULFILMENT:
        return
    from app.modules.dispatch.models import DeliveryOrder, DeliveryOrderItem

    open_total = sum((i.base_qty - i.qty_cancelled_base for i in order.items), ZERO)
    dispatched = _dispatched(order)
    settled = db.execute(
        select(func.coalesce(func.sum(DeliveryOrderItem.dispatched_base), 0))
        .join(DeliveryOrder, DeliveryOrder.id == DeliveryOrderItem.do_id)
        .where(DeliveryOrder.sales_order_id == order.id,
               DeliveryOrder.status.in_(["DELIVERED", "PARTIALLY_DELIVERED"]))
    ).scalar_one()
    if dispatched == 0:
        new = "APPROVED"
    elif settled == 0:
        new = "DISPATCHED" if dispatched >= open_total else "PARTIALLY_DISPATCHED"
    elif settled >= open_total:
        new = "DELIVERED"
    else:
        new = "PARTIALLY_DELIVERED"
    for i in order.items:
        if i.status == "OPEN" and i.qty_dispatched_base >= i.base_qty - i.qty_cancelled_base and new == "DELIVERED":
            i.status = "FULFILLED"
    if new != order.status:
        _set_status(db, order, new, "fulfilment", ctx)


def allowed_actions(db: Session, ctx: Ctx, order: SalesOrder) -> list[str]:
    s, can = order.status, []
    own = order.created_by == ctx.user_id
    if s == "DRAFT" and ctx.has("orders.create"):
        can += ["edit", "submit", "cancel"]
    if s in ("UNDER_REVIEW", "ON_HOLD") and ctx.has("orders.approve") and (not own or ctx.has("orders.self_approval")):
        if s == "UNDER_REVIEW" or (order.hold_kind != "MANUAL" and ctx.has("credit.override")):
            can.append("approve")
        can.append("reject")
    if s == "ON_HOLD" and order.hold_kind == "MANUAL" and ctx.has("orders.hold"):
        can.append("release")
    if s in ("UNDER_REVIEW", "APPROVED", "PARTIALLY_DISPATCHED") and ctx.has("orders.hold"):
        can.append("hold")
    if (s in ("UNDER_REVIEW", "ON_HOLD") and ctx.has("orders.cancel")) or (
            s == "APPROVED" and ctx.has("orders.cancel_approved")):
        can.append("cancel")
    if s in OPEN_FULFILMENT and ctx.has("dispatch.plan"):
        can.append("plan_dispatch")
    if s in OPEN_FULFILMENT + ["DELIVERED"] and ctx.has("orders.close") and _dispatched(order) > 0:
        can.append("close")
    return can
