import re
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, require
from app.modules.masters.models import Dealer, DealerContact, Vehicle
from app.modules.masters.service import assigned_dealer_ids_subquery
from app.modules.orders import service as orders_service
from app.modules.reports import sales as sales_report
from app.modules.reports import statement as stmt_svc

router = APIRouter(tags=["reports"])


def _today() -> date:
    from datetime import datetime

    return datetime.now(sales_report.IST).date()


@router.get("/reports/sales")
def sales(date_from: date | None = None, date_to: date | None = None, group: str = "day",
          dealer_id: uuid.UUID | None = None, format: str = Query("json", pattern="^(json|xlsx)$"),  # noqa: A002
          ctx: Ctx = Depends(require("reports.view")), db: Session = Depends(get_db)):
    """Bags, tonnes and amount delivered, grouped by day / dealer / product / salesperson / godown.
    Default: this month so far. format=xlsx downloads it as Excel."""
    d_to = date_to or _today()
    d_from = date_from or d_to.replace(day=1)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    d_from = max(d_from, d_to - timedelta(days=730))
    rep = sales_report.report(db, ctx.company_id, d_from, d_to, group, [dealer_id] if dealer_id else None)
    if format == "xlsx":
        name = f"sales_{d_from:%Y%m%d}_{d_to:%Y%m%d}_{rep['group']}.xlsx"
        return Response(sales_report.to_xlsx(rep), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return rep


@router.get("/reports/daily")
def daily_report(day: date | None = None, date_to: date | None = None,
                 format: str = Query("json", pattern="^(json|xlsx)$"),  # noqa: A002
                 ctx: Ctx = Depends(require("reports.view")), db: Session = Depends(get_db)):
    """Dealer-wise sales for a day (default today): product, quantity, rate, amount. date_to for a few days."""
    from app.modules.masters.models import Company

    d = day or _today()
    d_to = date_to if date_to and date_to >= d else d
    d_to = min(d_to, d + timedelta(days=31))
    rep = sales_report.daily(db, ctx.company_id, d, d_to)
    if format == "xlsx":
        co = db.get(Company, ctx.company_id)
        name = f"dealerwise_sales_{d:%Y%m%d}" + ("" if d_to == d else f"_{d_to:%Y%m%d}") + ".xlsx"
        return Response(sales_report.daily_xlsx(rep, co.trade_name or co.legal_name),
                        headers={"Content-Disposition": f'attachment; filename="{name}"'},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return rep


@router.get("/dealers/{dealer_id}/statement")
def dealer_statement(dealer_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None,
                     ctx: Ctx = Depends(require("credit.view")), db: Session = Depends(get_db)):
    """The dealer's ledger in Tally for the period (default: the last 3 months)."""
    dealer = orders_service.dealer_visible(db, ctx, dealer_id)
    d_to = date_to or _today()
    d_from = date_from or (d_to - timedelta(days=90))
    return stmt_svc.statement(db, ctx.company_id, dealer, d_from, d_to)


@router.get("/search")
def search(q: str = Query(..., min_length=2, max_length=60), ctx: Ctx = Depends(require()),
           db: Session = Depends(get_db)):
    """One box for everything: order number, dealer (name or mobile), truck number, delivery order, invoice."""
    from app.modules.dispatch.models import DeliveryOrder
    from app.modules.orders.models import SalesOrder
    from app.modules.purchases.models import DirectTruck

    term = q.strip()
    like = f"%{term}%"
    squash = re.sub(r"[\s\-/]", "", term)  # "DL 1L A 1234" finds DL1LA1234
    slike = f"%{squash}%"
    digits = re.sub(r"\D", "", term)
    co = ctx.company_id
    out: dict[str, list] = {"orders": [], "dealers": [], "trucks": [], "direct_trucks": []}

    def visible_dealers(stmt, col):
        if ctx.has("dealers.view_all"):
            return stmt
        return stmt.where(col.in_(assigned_dealer_ids_subquery(ctx.user.employee_id)))

    if ctx.has("masters.view") or ctx.has("orders.view"):
        conds = [Dealer.name.ilike(like), Dealer.tally_ledger_name.ilike(like), Dealer.code.ilike(like)]
        if len(digits) >= 5:
            conds += [Dealer.tally_mobile.ilike(f"%{digits}%"),
                      Dealer.id.in_(select(DealerContact.dealer_id).where(
                          func.regexp_replace(DealerContact.phone, r"\D", "", "g").ilike(f"%{digits}%")))]
        rows = db.execute(visible_dealers(select(Dealer).where(Dealer.company_id == co, or_(*conds)), Dealer.id)
                          .order_by(Dealer.name).limit(8)).scalars()
        out["dealers"] = [{"id": d.id, "name": d.name, "balance": d.tally_balance, "status": d.status,
                           "link": f"/dealers/{d.id}"} for d in rows]

    if ctx.has("orders.view"):
        stmt = orders_service.orders_visible(select(SalesOrder).where(SalesOrder.company_id == co), ctx)
        stmt = stmt.join(Dealer, Dealer.id == SalesOrder.dealer_id).where(
            or_(SalesOrder.order_no.ilike(like), func.replace(SalesOrder.order_no, "/", "").ilike(slike),
                SalesOrder.site_name.ilike(like)))
        for o in db.execute(stmt.order_by(SalesOrder.created_at.desc()).limit(8)).scalars():
            d = db.get(Dealer, o.dealer_id)
            out["orders"].append({"id": o.id, "order_no": o.order_no, "dealer_name": d.name, "status": o.status,
                                  "order_date": o.order_date, "total_amount": o.total_amount,
                                  "link": f"/orders/{o.id}"})

    if ctx.has("dispatch.view"):
        stmt = (select(DeliveryOrder).outerjoin(Vehicle, Vehicle.id == DeliveryOrder.vehicle_id)
                .outerjoin(DirectTruck, DirectTruck.id == DeliveryOrder.direct_truck_id)
                .where(DeliveryOrder.company_id == co,
                       or_(DeliveryOrder.do_no.ilike(like), func.replace(DeliveryOrder.do_no, "/", "").ilike(slike),
                           func.replace(Vehicle.registration_no, " ", "").ilike(slike),
                           func.replace(DirectTruck.truck_no, " ", "").ilike(slike),
                           DeliveryOrder.supplier_ref.ilike(like))))
        if ctx.godown_ids is not None:
            stmt = stmt.where(DeliveryOrder.godown_id.in_(ctx.godown_ids))
        from app.modules.dispatch.service import vehicle_label

        for d in db.execute(stmt.order_by(DeliveryOrder.created_at.desc()).limit(8)).scalars():
            out["trucks"].append({"id": d.id, "do_no": d.do_no, "vehicle": vehicle_label(db, d), "status": d.status,
                                  "dealer_name": db.get(Dealer, d.dealer_id).name, "link": f"/dispatch/{d.id}"})
        for t in db.execute(select(DirectTruck).where(
                DirectTruck.company_id == co,
                or_(func.replace(DirectTruck.truck_no, " ", "").ilike(slike), DirectTruck.invoice_no.ilike(like),
                    DirectTruck.ref.ilike(like))).order_by(DirectTruck.invoice_date.desc()).limit(6)).scalars():
            out["direct_trucks"].append({"id": t.id, "truck_no": t.truck_no, "invoice_no": t.invoice_no,
                                         "status": t.status, "link": "/stock?tab=trucks"})
    return out
