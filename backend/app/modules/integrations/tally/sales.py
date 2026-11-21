"""Delivered orders → Tally Sales vouchers, waiting for an admin's approval inside Tally (decision D23).

When an order is fully delivered (or closed after part-delivery) it is queued and pushed straight away as one
*Optional* Sales voucher: party = the dealer's Tally ledger, one line per item with the delivered quantity at the
order's rate, taken from the godown(s) its trucks left from. Optional vouchers show in Tally's Day Book but do not
touch stock or the dealer's balance. The admin opens it in Tally and either marks it Regular (approved) or deletes
it (rejected). "Check Tally" reads that back.
"""

import datetime as dt
import traceback
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.db import SessionLocal
from app.modules.integrations.tally.models import TallySale
from app.modules.masters.models import Company, CompanyProduct, Dealer, Godown, Product
from app.modules.masters.tally_products import map_unit
from app.modules.orders.models import SalesOrder
from app.modules.platform.settings import get_setting
from app.modules.purchases import service as psvc
from app.modules.purchases import tally_io

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CENT = Decimal("0.01")
SETTLED = ["DELIVERED", "PARTIALLY_DELIVERED"]


def now() -> datetime:
    return datetime.now(UTC)


def _e(s) -> str:
    return escape(str(s or ""))


# ---------------------------------------------------------------- queue
def queue(db: Session, order: SalesOrder) -> TallySale | None:
    """Called when an order becomes DELIVERED, or CLOSED with something delivered. Idempotent."""
    existing = db.execute(select(TallySale).where(TallySale.order_id == order.id)).scalar_one_or_none()
    if existing is not None:
        if existing.status in ("FAILED", "REJECTED"):
            existing.status, existing.error = "PENDING", None
        return existing
    if not any(i.qty_delivered_base > 0 for i in order.items):
        return None
    sale = TallySale(company_id=order.company_id, order_id=order.id, status="PENDING", queued_at=now())
    db.add(sale)
    db.flush()
    return sale


# ---------------------------------------------------------------- voucher
def build(db: Session, order: SalesOrder, units: dict[str, str], sales_ledger: str, vtype: str,
          tally_godowns: list[str] | None = None, tally_ledgers: list[str] | None = None,
          ) -> tuple[str, Decimal, dt.date]:
    """(voucher XML, total, voucher date) for what was actually delivered on this order.

    Every stock item and godown named must exist in Tally: TallyPrime files a voucher that names one it doesn't
    have under Exceptions, without its items. So a missing one stops the send with a clear message instead."""
    from app.modules.dispatch.models import DeliveryOrder, DeliveryOrderItem

    dealer = db.get(Dealer, order.dealer_id)
    party = dealer.tally_ledger_name or dealer.name
    if tally_ledgers:
        real = next((x for x in tally_ledgers if x.lower() == party.lower()), None)
        if real is None:
            raise ValueError(f"Dealer “{party}” has no ledger in Tally yet. Sync with Tally (a dealer added in the "
                             "ERP is made in Tally then), then send again.")
        party = real
    rows = db.execute(
        select(DeliveryOrderItem, DeliveryOrder).join(DeliveryOrder, DeliveryOrder.id == DeliveryOrderItem.do_id)
        .where(DeliveryOrder.sales_order_id == order.id, DeliveryOrder.status.in_(SETTLED))).all()
    if not rows:
        raise ValueError("Nothing delivered on this order")
    items = {i.id: i for i in order.items}
    godowns = {g.id: (g.tally_godown_name or g.name) for g in db.execute(
        select(Godown).where(Godown.id.in_({d.godown_id for _, d in rows}))).scalars()}
    trucks = []
    per_line: dict = defaultdict(lambda: defaultdict(Decimal))  # order line → godown → delivered base
    last = None
    for it, d in rows:
        if it.delivered_base > 0:
            per_line[it.order_item_id][godowns[d.godown_id]] += it.delivered_base
        last = max(last, d.delivered_at) if last and d.delivered_at else (d.delivered_at or last)
        trucks.append(d.do_no)
    day = (last or now()).astimezone(IST).date()

    inv_xml, total = "", Decimal(0)
    for line_id, by_godown in per_line.items():
        oi = items[line_id]
        cp = db.get(CompanyProduct, (order.company_id, oi.product_id))
        product = db.get(Product, oi.product_id)
        item = (cp.tally_stock_item_name if cp else None) or product.name
        if units:
            real = next((k for k in units if k.lower() == item.lower()), None)
            if real is None:
                raise ValueError(f"“{item}” is not a stock item in Tally. On the Products page, link {product.name} "
                                 "to its Tally stock item (or sync with Tally), then send again.")
            item = real
        unit = units.get(item, "")
        tally_uom = map_unit(unit)
        delivered = sum(by_godown.values(), Decimal(0))
        # amount at the order's rate for what was delivered
        amount = (oi.rate * oi.rate_qty * delivered / oi.base_qty).quantize(CENT, ROUND_HALF_UP)
        qty_total = _in_tally_unit(db, oi, product, tally_uom, delivered)
        rate = (amount / qty_total).quantize(Decimal("0.0001")) if qty_total else Decimal(0)
        batches = ""
        if tally_godowns:
            known = {x.lower(): x for x in tally_godowns}
            missing = [g for g in by_godown if g.lower() not in known]
            if missing:
                raise ValueError(f"Godown “{missing[0]}” is not in Tally. Fix its Tally name on the Godowns page "
                                 "(or sync with Tally), then send again.")
            by_godown = {known[g.lower()]: q for g, q in by_godown.items()}
        for g, base in by_godown.items():
            q = _in_tally_unit(db, oi, product, tally_uom, base)
            a = (amount * base / delivered).quantize(CENT, ROUND_HALF_UP)
            batches += (f"<BATCHALLOCATIONS.LIST><GODOWNNAME>{_e(g)}</GODOWNNAME><BATCHNAME>Primary Batch</BATCHNAME>"
                        f"<AMOUNT>{a:.2f}</AMOUNT><ACTUALQTY>{_q(q, unit)}</ACTUALQTY><BILLEDQTY>{_q(q, unit)}</BILLEDQTY>"
                        f"</BATCHALLOCATIONS.LIST>")
        rate_txt = f"{rate.normalize():f}" + (f"/{unit}" if unit else "")
        inv_xml += f"""<ALLINVENTORYENTRIES.LIST>
<STOCKITEMNAME>{_e(item)}</STOCKITEMNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<RATE>{rate_txt}</RATE><AMOUNT>{amount:.2f}</AMOUNT><ACTUALQTY>{_q(qty_total, unit)}</ACTUALQTY><BILLEDQTY>{_q(qty_total, unit)}</BILLEDQTY>
{batches}
<ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>{_e(sales_ledger)}</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{amount:.2f}</AMOUNT></ACCOUNTINGALLOCATIONS.LIST>
</ALLINVENTORYENTRIES.LIST>
"""
        total += amount
    if not inv_xml or total <= 0:  # never send a voucher with only the dealer's side (Tally lists it as an exception)
        raise ValueError("No delivered quantity with a value on this order, so there is nothing to bill in Tally")
    ref = order.order_no or str(order.id)
    narr = f"ERP order {ref} - delivered by {', '.join(sorted(set(trucks)))}"
    if order.site_name:
        narr += f" - site {order.site_name}"
    xml = f"""<VOUCHER VCHTYPE="{_e(vtype)}" ACTION="Create" OBJVIEW="Invoice Voucher View">
<DATE>{day:%Y%m%d}</DATE><EFFECTIVEDATE>{day:%Y%m%d}</EFFECTIVEDATE><VOUCHERTYPENAME>{_e(vtype)}</VOUCHERTYPENAME>
<REFERENCE>{_e(ref)}</REFERENCE><REFERENCEDATE>{day:%Y%m%d}</REFERENCEDATE>
<PARTYLEDGERNAME>{_e(party)}</PARTYLEDGERNAME><PARTYNAME>{_e(party)}</PARTYNAME>
<BASICBUYERNAME>{_e(party)}</BASICBUYERNAME><BASICBASEPARTYNAME>{_e(party)}</BASICBASEPARTYNAME>
<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW><ISINVOICE>Yes</ISINVOICE><ISOPTIONAL>Yes</ISOPTIONAL>
<NARRATION>{_e(narr)}</NARRATION>
<LEDGERENTRIES.LIST><LEDGERNAME>{_e(party)}</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-{total:.2f}</AMOUNT><BILLALLOCATIONS.LIST><NAME>{_e(ref)}</NAME>
<BILLTYPE>New Ref</BILLTYPE><AMOUNT>-{total:.2f}</AMOUNT></BILLALLOCATIONS.LIST></LEDGERENTRIES.LIST>
{inv_xml}</VOUCHER>"""
    return xml, total, day


def _in_tally_unit(db: Session, oi, product: Product, tally_uom: str | None, base: Decimal) -> Decimal:
    """Delivered quantity (product base unit) in the stock item's Tally unit."""
    if tally_uom in (None, oi.entered_uom):
        return (base / oi.conversion_factor).quantize(Decimal("0.001"))
    if tally_uom == oi.base_uom:
        return base
    per = psvc.to_base(db, product, Decimal(1), tally_uom)  # base units in one Tally unit
    return (base / per if per else base / oi.conversion_factor).quantize(Decimal("0.001"))


def _q(q: Decimal, unit: str) -> str:
    txt = f"{q.normalize():f}" if q != q.to_integral() else f"{int(q)}"
    return f" {txt} {unit}".rstrip()


def _ledger(db: Session, company_id: uuid.UUID, m: dict) -> str:
    want = (get_setting(db, company_id, "tally.sales_ledger") or "").strip()
    names = {x["name"].lower(): x["name"] for x in m["ledgers"]}
    if want:
        hit = names.get(want.lower())
        if hit is None:
            raise ValueError(f"Sales ledger “{want}” is not in Tally. Fix it on the Tally sales page.")
        return hit
    sales = [x["name"] for x in m["ledgers"] if x["parent"].lower() == "sales accounts"]
    if len(sales) == 1:
        return sales[0]
    raise ValueError("Choose the Tally sales ledger on the Tally sales page"
                     + (f" ({', '.join(sales[:6])})" if sales else ""))


def _client(db: Session, company_id: uuid.UUID) -> tally_io.Tally:
    cfg = psvc.load_cfg(db, company_id)
    company = db.get(Company, company_id)
    return tally_io.Tally(psvc.tally_url(db, company_id, cfg), company.tally_company_name or cfg.get("company", ""))


# ---------------------------------------------------------------- push
def push_pending(db: Session, company_id: uuid.UUID, actor_id=None, ids: list[uuid.UUID] | None = None) -> dict:
    """Push every PENDING (and, when asked, FAILED) sale. Each one is committed on its own."""
    stmt = select(TallySale).where(TallySale.company_id == company_id)
    stmt = stmt.where(TallySale.id.in_(ids)) if ids else stmt.where(TallySale.status == "PENDING")
    sales = list(db.execute(stmt.order_by(TallySale.queued_at)).scalars())
    if not sales:
        return {"pushed": 0, "failed": 0}
    done = failed = 0
    try:
        c = _client(db, company_id)
        m = c.masters()
        ledger = _ledger(db, company_id, m)
    except (tally_io.TallyError, ValueError) as e:
        for s in sales:
            if s.status in ("PENDING", "FAILED"):
                s.status, s.error, s.attempts = "FAILED", str(e), s.attempts + 1
        db.commit()
        return {"pushed": 0, "failed": len(sales), "error": str(e)}
    units = {x["name"]: x["unit"] for x in m["items"]}
    vtype = get_setting(db, company_id, "tally.sales_voucher_type") or "Sales"
    for s in sales:
        if s.status not in ("PENDING", "FAILED"):
            continue
        order = db.get(SalesOrder, s.order_id)
        s.attempts += 1
        try:
            xml, total, day = build(db, order, units, ledger, vtype,
                                    [g["name"] if isinstance(g, dict) else str(g) for g in m.get("godowns") or []],
                                    [x["name"] for x in m["ledgers"]])
            s.xml, s.amount = xml, f"{total:.2f}"
            s.voucher_date = datetime.combine(day, dt.time(12), IST)
            r = c.import_voucher(xml)
        except (tally_io.TallyError, ValueError) as e:
            r = {"ok": False, "error": str(e)}
        if r["ok"]:
            # Made in Tally. With a warning it is under Tally's Exceptions: fix or delete it there; it is never sent
            # again by itself (that would make a second voucher).
            s.status, s.error, s.tally_master_id, s.pushed_at = ("WAITING_APPROVAL", r.get("warning"),
                                                                 r.get("vchid") or None, now())
            done += 1
        else:
            s.status, s.error = "FAILED", r["error"]
            failed += 1
        write_audit(db, action="tally_sale.push", entity_type="tally_sales", entity_id=s.id, actor_id=actor_id,
                    company_id=company_id, after={"order": order.order_no, "ok": r["ok"], "error": r.get("error")})
        db.commit()
    return {"pushed": done, "failed": failed}


def push_in_background(company_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        push_pending(db, company_id)
    except Exception:  # noqa: BLE001 - a background push must never crash the server
        traceback.print_exc()
    finally:
        db.close()


# ---------------------------------------------------------------- approval read-back
def check(db: Session, company_id: uuid.UUID) -> dict:
    """Read Tally: optional still = waiting; regular = approved; gone = rejected (deleted in Tally)."""
    waiting = list(db.execute(select(TallySale).where(TallySale.company_id == company_id,
                                                      TallySale.status == "WAITING_APPROVAL")).scalars())
    if not waiting:
        return {"checked": 0}
    days = [w.voucher_date.astimezone(IST).date() for w in waiting if w.voucher_date]
    c = _client(db, company_id)
    root = c._collection("Voucher", "Date,VoucherNumber,Reference,MasterId,IsOptional,IsCancelled,VoucherTypeName",
                         filt="$$IsSales:$VoucherTypeName", from_date=min(days), to_date=max(days) + dt.timedelta(days=1))
    by_master, by_ref = {}, {}
    for v in root.iter("VOUCHER"):
        row = {"optional": (v.findtext("ISOPTIONAL") or "").strip().lower() == "yes",
               "cancelled": (v.findtext("ISCANCELLED") or "").strip().lower() == "yes",
               "number": (v.findtext("VOUCHERNUMBER") or "").strip()}
        mid = (v.findtext("MASTERID") or "").strip()
        if mid:
            by_master[mid] = row
        ref = (v.findtext("REFERENCE") or "").strip()
        if ref:
            by_ref.setdefault(ref, row)
    # A missing voucher means "deleted in Tally" only if this Tally lists optional vouchers at all; otherwise a
    # voucher still waiting would look deleted.
    optional_visible = any(r["optional"] for r in [*by_master.values(), *by_ref.values()])
    out = {"checked": len(waiting), "approved": 0, "rejected": 0, "waiting": 0}
    for w in waiting:
        order = db.get(SalesOrder, w.order_id)
        row = by_master.get(w.tally_master_id or "") or by_ref.get(order.order_no or "")
        w.checked_at = now()
        if row and not row["optional"] and not row["cancelled"]:
            w.status, w.decided_at, w.tally_voucher_no = "APPROVED", now(), row["number"] or None
            out["approved"] += 1
        elif row and row["cancelled"]:
            w.status, w.decided_at, w.error = "REJECTED", now(), "Cancelled in Tally"
            out["rejected"] += 1
        elif row is None and optional_visible:
            w.status, w.decided_at, w.error = "REJECTED", now(), "No longer in Tally (deleted)"
            out["rejected"] += 1
        else:
            out["waiting"] += 1
        if w.status == "REJECTED":
            from app.modules.platform import notify

            notify.tally_sale_rejected(db, w, order)
        if w.status != "WAITING_APPROVAL":
            write_audit(db, action=f"tally_sale.{w.status.lower()}", entity_type="tally_sales", entity_id=w.id,
                        actor_id=None, company_id=company_id, after={"order": order.order_no})
    db.commit()
    return out
