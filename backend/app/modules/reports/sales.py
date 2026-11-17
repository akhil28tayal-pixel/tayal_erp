"""Sales report: what was delivered (from the ERP's own delivery records), grouped by day, dealer, product,
salesperson or godown, with the previous period of the same length for comparison."""

import io
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.dispatch.models import DeliveryOrder, DeliveryOrderItem
from app.modules.masters.models import Dealer, Employee, Godown, Product
from app.modules.orders.models import SalesOrder, SalesOrderItem

IST = ZoneInfo("Asia/Kolkata")
GROUPS = ("day", "dealer", "product", "salesperson", "godown")


def _bounds(d_from: date, d_to: date) -> tuple[datetime, datetime]:
    return (datetime.combine(d_from, time.min, IST).astimezone(UTC),
            datetime.combine(d_to + timedelta(days=1), time.min, IST).astimezone(UTC))


def _lines(db: Session, company_id: uuid.UUID, d_from: date, d_to: date, dealer_ids=None):
    a, b = _bounds(d_from, d_to)
    q = (select(DeliveryOrderItem, DeliveryOrder, SalesOrderItem, SalesOrder)
         .join(DeliveryOrder, DeliveryOrder.id == DeliveryOrderItem.do_id)
         .join(SalesOrderItem, SalesOrderItem.id == DeliveryOrderItem.order_item_id)
         .join(SalesOrder, SalesOrder.id == DeliveryOrder.sales_order_id)
         .where(DeliveryOrder.company_id == company_id, DeliveryOrder.status == "DELIVERED",
                DeliveryOrder.delivered_at >= a, DeliveryOrder.delivered_at < b, DeliveryOrderItem.delivered_base > 0))
    if dealer_ids is not None:
        q = q.where(DeliveryOrder.dealer_id.in_(dealer_ids))
    return db.execute(q).all()


def _amount(oi: SalesOrderItem, base: Decimal) -> Decimal:
    # the order line's rate is per rate_uom; rate_qty is the line's quantity in that unit
    per_base = (oi.rate_qty / oi.base_qty) if oi.base_qty else Decimal(0)
    return (base * per_base * oi.rate).quantize(Decimal("0.01"))


def _kg(p: Product, base: Decimal) -> Decimal:
    if p.base_uom == "KG":
        return base
    if p.base_uom == "MT":
        return base * 1000
    if p.bag_weight_kg:
        return base * p.bag_weight_kg
    return Decimal(0)


def report(db: Session, company_id: uuid.UUID, d_from: date, d_to: date, group: str, dealer_ids=None) -> dict:
    if group not in GROUPS:
        group = "day"
    cache: dict = {}

    def get(model, ident):
        k = (model, ident)
        if k not in cache:
            cache[k] = db.get(model, ident) if ident else None
        return cache[k]

    def key_label(do: DeliveryOrder, so: SalesOrder, p: Product) -> tuple[str, str]:
        if group == "day":
            d = do.delivered_at.astimezone(IST).date()
            return d.isoformat(), d.strftime("%a %d %b")
        if group == "dealer":
            return str(do.dealer_id), get(Dealer, do.dealer_id).name
        if group == "product":
            return str(p.id), p.name
        if group == "godown":
            g = get(Godown, do.godown_id)
            return str(do.godown_id), g.name if g else "-"
        e = get(Employee, so.sales_exec_id)
        return (str(e.id), e.full_name) if e else ("-", "Not assigned")

    def collect(rows):
        agg = defaultdict(lambda: {"bags": Decimal(0), "kg": Decimal(0), "amount": Decimal(0), "trucks": set(),
                                   "orders": set(), "dealers": set()})
        for di, do, oi, so in rows:
            p = get(Product, di.product_id)
            k, label = key_label(do, so, p)
            a = agg[(k, label)]
            if p.base_uom == "BAG":
                a["bags"] += di.delivered_base
            a["kg"] += _kg(p, di.delivered_base)
            a["amount"] += _amount(oi, di.delivered_base)
            a["trucks"].add(do.id)
            a["orders"].add(so.id)
            a["dealers"].add(do.dealer_id)
        return agg

    def row(k, label, a):
        return {"key": k, "label": label, "bags": f"{a['bags']:.0f}", "tonnes": f"{a['kg'] / 1000:.2f}",
                "amount": f"{a['amount']:.2f}", "trucks": len(a["trucks"]), "orders": len(a["orders"]),
                "dealers": len(a["dealers"])}

    def total(agg):
        t = {"bags": Decimal(0), "kg": Decimal(0), "amount": Decimal(0), "trucks": set(), "orders": set(),
             "dealers": set()}
        for a in agg.values():
            for f in ("bags", "kg", "amount"):
                t[f] += a[f]
            for f in ("trucks", "orders", "dealers"):
                t[f] |= a[f]
        return row("total", "Total", t)

    cur = collect(_lines(db, company_id, d_from, d_to, dealer_ids))
    days = (d_to - d_from).days + 1
    prev_to = d_from - timedelta(days=1)
    prev = collect(_lines(db, company_id, prev_to - timedelta(days=days - 1), prev_to, dealer_ids))
    rows = [row(k, label, a) for (k, label), a in cur.items()]
    rows.sort(key=lambda r: r["key"] if group == "day" else -float(r["amount"]))
    return {"from": d_from, "to": d_to, "group": group, "rows": rows, "total": total(cur),
            "previous": {**total(prev), "from": prev_to - timedelta(days=days - 1), "to": prev_to}}


def to_xlsx(rep: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    heading = {"day": "Day", "dealer": "Dealer", "product": "Product", "salesperson": "Salesperson",
               "godown": "Godown"}[rep["group"]]
    ws.append([f"Sales delivered {rep['from']:%d %b %Y} to {rep['to']:%d %b %Y}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append([heading, "Bags", "Tonnes", "Amount (₹)", "Trucks", "Orders", "Dealers"])
    for c in ws[3]:
        c.font = Font(bold=True)
    for r in [*rep["rows"], rep["total"]]:
        ws.append([r["label"], int(Decimal(r["bags"])), float(r["tonnes"]), float(r["amount"]), r["trucks"],
                   r["orders"], r["dealers"]])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for col, w in zip("ABCDEFG", (34, 10, 10, 16, 8, 8, 8), strict=True):
        ws.column_dimensions[col].width = w
    for cells in ws.iter_rows(min_row=4, min_col=4, max_col=4):
        cells[0].number_format = "#,##,##0.00"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ daily dealer-wise
def _q(x: Decimal) -> str:
    s = f"{x.quantize(Decimal('0.001')):f}".rstrip("0").rstrip(".")
    return s or "0"


def daily(db: Session, company_id: uuid.UUID, d_from: date, d_to: date | None = None) -> dict:
    """Each dealer's deliveries for the day (or days): product, quantity, rate, amount; dealer and grand totals.
    Quantity is in the unit the order was booked in; the rate is the order's rate (per its rate unit)."""
    d_to = d_to or d_from
    rows = _lines(db, company_id, d_from, d_to)
    dealers: dict = {}
    for di, do, oi, so in rows:
        p = db.get(Product, di.product_id)
        qty = di.delivered_base / oi.conversion_factor if oi.conversion_factor else di.delivered_base
        key = (p.id, oi.entered_uom, oi.rate, oi.rate_uom)
        d = dealers.setdefault(do.dealer_id, {"lines": {}, "orders": set(), "trucks": set()})
        ln = d["lines"].setdefault(key, {"product": p.name, "unit": oi.entered_uom, "qty": Decimal(0),
                                         "rate": oi.rate, "rate_unit": oi.rate_uom, "amount": Decimal(0),
                                         "bags": Decimal(0), "kg": Decimal(0), "orders": set()})
        ln["qty"] += qty
        ln["amount"] += _amount(oi, di.delivered_base)
        ln["kg"] += _kg(p, di.delivered_base)
        if p.base_uom == "BAG":
            ln["bags"] += di.delivered_base
        ln["orders"].add(so.order_no)
        d["orders"].add(so.order_no)
        d["trucks"].add(do.id)
    out = []
    for dealer_id, d in dealers.items():
        dealer = db.get(Dealer, dealer_id)
        lines = sorted(d["lines"].values(), key=lambda x: x["product"])
        amount = sum((x["amount"] for x in lines), Decimal(0))
        out.append({
            "dealer_id": dealer_id, "dealer": dealer.name, "orders": sorted(o for o in d["orders"] if o),
            "trucks": len(d["trucks"]), "amount": f"{amount:.2f}",
            "bags": f"{sum((x['bags'] for x in lines), Decimal(0)):.0f}",
            "tonnes": f"{sum((x['kg'] for x in lines), Decimal(0)) / 1000:.2f}",
            "lines": [{"product": x["product"], "qty": _q(x["qty"]), "unit": x["unit"], "rate": f"{x['rate']:.2f}",
                       "rate_unit": x["rate_unit"], "amount": f"{x['amount']:.2f}",
                       "orders": sorted(o for o in x["orders"] if o)} for x in lines],
        })
    out.sort(key=lambda x: x["dealer"].lower())
    total = {"amount": f"{sum((Decimal(x['amount']) for x in out), Decimal(0)):.2f}",
             "bags": f"{sum((Decimal(x['bags']) for x in out), Decimal(0)):.0f}",
             "tonnes": f"{sum((Decimal(x['tonnes']) for x in out), Decimal(0)):.2f}",
             "trucks": sum(x["trucks"] for x in out), "dealers": len(out)}
    # the same products added up across dealers
    by_product: dict = {}
    for x in out:
        for ln in x["lines"]:
            b = by_product.setdefault((ln["product"], ln["unit"]), {"product": ln["product"], "unit": ln["unit"],
                                                                     "qty": Decimal(0), "amount": Decimal(0)})
            b["qty"] += Decimal(ln["qty"])
            b["amount"] += Decimal(ln["amount"])
    products = [{"product": b["product"], "unit": b["unit"], "qty": _q(b["qty"]), "amount": f"{b['amount']:.2f}"}
                for b in sorted(by_product.values(), key=lambda b: b["product"])]
    return {"from": d_from, "to": d_to, "dealers": out, "total": total, "products": products}


def daily_xlsx(rep: dict, company_name: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Dealer-wise"
    day = f"{rep['from']:%d %b %Y}" + ("" if rep["to"] == rep["from"] else f" to {rep['to']:%d %b %Y}")
    ws.append([f"{company_name} — dealer-wise sales, {day}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append(["Dealer", "Product", "Quantity", "Unit", "Rate (₹)", "Per", "Amount (₹)", "Order(s)"])
    for c in ws[3]:
        c.font = Font(bold=True)
    shade = PatternFill("solid", fgColor="E6EEFF")
    for d in rep["dealers"]:
        for i, ln in enumerate(d["lines"]):
            ws.append([d["dealer"] if i == 0 else "", ln["product"], float(ln["qty"]), ln["unit"], float(ln["rate"]),
                       ln["rate_unit"], float(ln["amount"]), ", ".join(ln["orders"])])
        ws.append([f"{d['dealer']} — total", "", None, "", None, "", float(d["amount"]), f"{d['trucks']} truck(s)"])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
            c.fill = shade
    ws.append([])
    ws.append(["GRAND TOTAL", f"{rep['total']['dealers']} dealers · {rep['total']['bags']} bags · "
               f"{rep['total']['tonnes']} t", None, "", None, "", float(rep["total"]["amount"]),
               f"{rep['total']['trucks']} truck(s)"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for col, w in zip("ABCDEFGH", (34, 28, 11, 7, 11, 6, 15, 28), strict=True):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=4):
        for c in (row[4], row[6]):
            c.number_format = "#,##,##0.00"
        row[2].alignment = Alignment(horizontal="right")
    ps = wb.create_sheet("By product")
    ps.append(["Product", "Quantity", "Unit", "Amount (₹)"])
    for c in ps[1]:
        c.font = Font(bold=True)
    for p in rep["products"]:
        ps.append([p["product"], float(p["qty"]), p["unit"], float(p["amount"])])
    ps.column_dimensions["A"].width = 30
    ps.column_dimensions["D"].width = 15
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
