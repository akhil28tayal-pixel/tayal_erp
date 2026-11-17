"""Godown stock as Tally has it, and what is left on each Direct truck.

Tally is the book of record (purchases are pushed there, sales bills are made there), so the ERP reads godown
stock back from Tally — on the "Refresh stock" button, after every Tally sync and after purchases are pushed —
and only subtracts what is already planned on trucks that have not left the gate yet.
"""

import traceback
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.modules.dispatch.models import DO_ACTIVE, DeliveryOrder, DeliveryOrderItem
from app.modules.masters.models import Company, Godown
from app.modules.masters.tally_products import map_unit
from app.modules.purchases import service, tally_io
from app.modules.purchases.models import DirectTruck, GodownStock, StockSnapshot

ZERO = Decimal(0)
STALE = timedelta(minutes=30)
# Set to a message to stop reading / showing Tally stock (e.g. while checking it against Tally).
PAUSED = ""


def latest(db: Session, company_id: uuid.UUID, ok_only: bool = False) -> StockSnapshot | None:
    if ok_only and PAUSED:
        return None
    stmt = select(StockSnapshot).where(StockSnapshot.company_id == company_id)
    if ok_only:
        stmt = stmt.where(StockSnapshot.status == "SUCCESS")
    return db.execute(stmt.order_by(StockSnapshot.started_at.desc())).scalars().first()


def running(db: Session, company_id: uuid.UUID) -> StockSnapshot | None:
    s = db.execute(select(StockSnapshot).where(StockSnapshot.company_id == company_id,
                                               StockSnapshot.status == "RUNNING")).scalars().first()
    if s and datetime.now(UTC) - s.started_at > STALE:
        s.status, s.finished_at, s.error = "FAILED", datetime.now(UTC), "Stopped: took too long or the server restarted"
        db.commit()
        return None
    return s


def start(db: Session, company_id: uuid.UUID, reason: str, actor_id) -> tuple[StockSnapshot, bool]:
    """(snapshot, started_now). A refresh already running is reused."""
    if PAUSED:
        s = StockSnapshot(company_id=company_id, status="FAILED", reason=reason, started_at=datetime.now(UTC),
                          finished_at=datetime.now(UTC), error=PAUSED, triggered_by=actor_id)
        db.add(s)
        db.commit()
        return s, False
    r = running(db, company_id)
    if r:
        return r, False
    s = StockSnapshot(company_id=company_id, status="RUNNING", reason=reason, started_at=datetime.now(UTC),
                      triggered_by=actor_id)
    db.add(s)
    db.commit()
    return s, True


def run(snapshot_id: uuid.UUID) -> None:
    """Background: read Tally, replace the stock rows. Own database session."""
    db = SessionLocal()
    try:
        snap = db.get(StockSnapshot, snapshot_id)
        company_id = snap.company_id
        try:
            cfg = service.load_cfg(db, company_id)
            company = db.get(Company, company_id)
            tally_name = company.tally_company_name or cfg.get("company")
            data = tally_io.Tally(service.tally_url(db, company_id, cfg), tally_name).godown_stock()
            summary = apply(db, company_id, snap, data)
            snap.status, snap.summary = "SUCCESS", summary
            # keep only this snapshot's rows
            db.execute(delete(GodownStock).where(GodownStock.company_id == company_id,
                                                 GodownStock.snapshot_id != snap.id))
        except tally_io.TallyError as e:
            db.rollback()
            snap = db.get(StockSnapshot, snapshot_id)
            snap.status, snap.error = "FAILED", str(e)
        except Exception as e:  # noqa: BLE001 - never leave it RUNNING
            db.rollback()
            snap = db.get(StockSnapshot, snapshot_id)
            snap.status, snap.error = "FAILED", f"Unexpected error: {e}"
            traceback.print_exc()
        snap.finished_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def apply(db: Session, company_id: uuid.UUID, snap: StockSnapshot, data: dict) -> dict:
    godowns = {(g.tally_godown_name or g.name).lower(): g
               for g in db.execute(select(Godown).where(Godown.company_id == company_id)).scalars()}
    products = service.product_by_tally_item(db, company_id)
    rows, unmatched_items = 0, set()
    for (gname, item), qty in data["stock"].items():
        if qty == 0:
            continue
        g = godowns.get(gname.lower())
        p = products.get(item.lower())
        unit = data["units"].get(item) or ""
        if p is None:
            unmatched_items.add(item)
        db.add(GodownStock(company_id=company_id, snapshot_id=snap.id, tally_godown=gname,
                           godown_id=g.id if g else None, tally_item=item, product_id=p.id if p else None,
                           qty=qty, tally_unit=unit or None, base_qty=service.to_base(db, p, qty, map_unit(unit))))
        rows += 1
    db.flush()
    return {"rows": rows, "vouchers": data.get("vouchers", 0), "from": data.get("from"), "to": data.get("to"),
            "skipped": data.get("skipped", {}), "items_not_in_erp": sorted(unmatched_items)[:50]}


def reserved(db: Session, company_id: uuid.UUID) -> dict[tuple, Decimal]:
    """Planned or loaded on trucks still at the godown, per (godown, product), in base units."""
    rows = db.execute(
        select(DeliveryOrder.godown_id, DeliveryOrderItem.product_id,
               func.sum(func.coalesce(DeliveryOrderItem.loaded_base, DeliveryOrderItem.planned_base)))
        .join(DeliveryOrderItem, DeliveryOrderItem.do_id == DeliveryOrder.id)
        .where(DeliveryOrder.company_id == company_id, DeliveryOrder.status.in_(DO_ACTIVE),
               DeliveryOrder.direct_truck_id.is_(None))
        .group_by(DeliveryOrder.godown_id, DeliveryOrderItem.product_id))
    return {(g, p): q for g, p, q in rows}


def stock_rows(db: Session, company_id: uuid.UUID) -> list[GodownStock]:
    if PAUSED:
        return []  # the last read is wrong; better nothing than wrong numbers
    snap = latest(db, company_id, ok_only=True)
    if snap is None:
        return []
    return list(db.execute(select(GodownStock).where(GodownStock.snapshot_id == snap.id)).scalars())


def availability(db: Session, company_id: uuid.UUID) -> dict[tuple, dict]:
    """{(godown_id, product_id): {"stock": base, "reserved": base, "available": base}}"""
    out: dict = defaultdict(lambda: {"stock": ZERO, "reserved": ZERO, "available": ZERO})
    for r in stock_rows(db, company_id):
        if r.godown_id and r.product_id and r.base_qty is not None:
            out[(r.godown_id, r.product_id)]["stock"] += r.base_qty
    for k, q in reserved(db, company_id).items():
        out[k]["reserved"] += q
    for v in out.values():
        v["available"] = v["stock"] - v["reserved"]
    return dict(out)


# ---------------------------------------------------------------- direct trucks
def truck_used(db: Session, truck_ids: list[uuid.UUID]) -> dict[tuple, Decimal]:
    """Base quantity already given to dealers from each truck, per (truck, product)."""
    if not truck_ids:
        return {}
    qty = case((DeliveryOrder.status.in_(DO_ACTIVE),
                func.coalesce(DeliveryOrderItem.loaded_base, DeliveryOrderItem.planned_base)),
               else_=DeliveryOrderItem.dispatched_base)
    rows = db.execute(
        select(DeliveryOrder.direct_truck_id, DeliveryOrderItem.product_id, func.sum(qty))
        .join(DeliveryOrderItem, DeliveryOrderItem.do_id == DeliveryOrder.id)
        .where(DeliveryOrder.direct_truck_id.in_(truck_ids), DeliveryOrder.status != "CANCELLED")
        .group_by(DeliveryOrder.direct_truck_id, DeliveryOrderItem.product_id))
    return {(t, p): q or ZERO for t, p, q in rows}


def truck_lines(db: Session, trucks: list[DirectTruck]) -> dict[uuid.UUID, list[dict]]:
    used = truck_used(db, [t.id for t in trucks])
    out = {}
    for t in trucks:
        lines = []
        for it in t.items:
            u = used.get((t.id, it.product_id), ZERO) if it.product_id else ZERO
            lines.append({"product_id": it.product_id, "tally_item": it.tally_item, "qty": it.qty, "uom": it.uom,
                          "base_qty": it.base_qty, "used_base": u,
                          "left_base": (it.base_qty - u) if it.base_qty is not None else None})
        out[t.id] = lines
    return out


def close_if_empty(db: Session, truck: DirectTruck) -> None:
    lines = truck_lines(db, [truck])[truck.id]
    if lines and all(ln["left_base"] is not None and ln["left_base"] <= 0 for ln in lines):
        truck.status, truck.close_note = "CLOSED", "Fully delivered"
