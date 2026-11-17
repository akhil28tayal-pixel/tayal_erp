"""Purchases (the Purchase Uploader, inside the ERP), godown stock from Tally, and Direct trucks."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.deps import Ctx, require
from app.core.errors import AppError, forbidden
from app.modules.dispatch.models import DeliveryOrder
from app.modules.masters.models import Company, Dealer, Godown, Product
from app.modules.purchases import service, stock, tally_io
from app.modules.purchases.models import DirectTruck, PurchaseFile

router = APIRouter(tags=["purchases"])
MAX_FILE = 5 * 1024 * 1024


def _any(ctx: Ctx, *perms: str) -> Ctx:
    if not any(ctx.has(p) for p in perms):
        raise forbidden(f"Missing permission: {perms[0]}")
    return ctx


def _tally_errors(fn):
    try:
        return fn()
    except tally_io.TallyError as e:
        raise AppError(502, "TALLY_ERROR", str(e)) from e


# ---------------------------------------------------------------- purchases
class MapIn(BaseModel):
    kind: Literal["ledger", "product", "godown"]
    supplier: Literal["ambuja", "shree"]
    text: str = Field(min_length=1, max_length=300)
    value: str = Field(min_length=1, max_length=200)
    keyword: str | None = Field(default=None, max_length=100)


class OverrideIn(BaseModel):
    id: str = Field(max_length=120)
    godown: str | None = Field(default=None, max_length=200)


class IdsIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class ForgetIn(BaseModel):
    ids: list[str] | None = Field(default=None, description="None = every invoice on the table")


class InvoiceIdIn(BaseModel):
    id: str = Field(max_length=120)


@router.get("/purchases", response_model=dict[str, Any])
def purchases(check_tally: bool = False, ctx: Ctx = Depends(require("inventory.import")), db: Session = Depends(get_db)):
    """The working table: every invoice in the uploaded files with its status. check_tally=true also checks Tally
    for duplicates and loads its ledgers / stock items / godowns (a few seconds)."""
    out = service.payload(db, ctx.company_id, check_tally)
    db.commit()
    return out


@router.post("/purchases/upload", response_model=dict[str, Any])
async def upload(files: list[UploadFile] = File(...), ctx: Ctx = Depends(require("inventory.import")),
                 db: Session = Depends(get_db)):
    for f in files:
        content = await f.read()
        if len(content) > MAX_FILE:
            raise AppError(413, "FILE_TOO_LARGE", f"{f.filename} is larger than 5 MB")
        name = (f.filename or "upload.xlsx")[:200]
        for old in db.execute(select(PurchaseFile).where(PurchaseFile.company_id == ctx.company_id,
                                                         PurchaseFile.cleared_at.is_(None),
                                                         PurchaseFile.name == name)).scalars():
            old.cleared_at = service.now()  # same file name again replaces the earlier copy, as in the app
        db.add(PurchaseFile(company_id=ctx.company_id, name=name, content=content, uploaded_by=ctx.user_id))
    db.flush()
    out = service.payload(db, ctx.company_id, check_tally=True)
    db.commit()
    return out


@router.post("/purchases/files/{file_id}/remove", response_model=dict[str, Any])
def remove_file(file_id: uuid.UUID, ctx: Ctx = Depends(require("inventory.import")), db: Session = Depends(get_db)):
    service.clear(db, ctx.company_id, file_id)
    out = service.payload(db, ctx.company_id)
    db.commit()
    return out


@router.post("/purchases/clear", response_model=dict[str, Any])
def clear(ctx: Ctx = Depends(require("inventory.import")), db: Session = Depends(get_db)):
    service.clear(db, ctx.company_id)
    out = service.payload(db, ctx.company_id)
    db.commit()
    return out


@router.post("/purchases/refresh", response_model=dict[str, Any])
def refresh(ctx: Ctx = Depends(require("inventory.import")), db: Session = Depends(get_db)):
    """Reload Tally's ledgers, stock items and godowns, and re-check for duplicates."""
    service.forget_cache(ctx.company_id)
    out = service.payload(db, ctx.company_id, check_tally=True)
    db.commit()
    return out


@router.post("/purchases/map", response_model=dict[str, Any])
def save_rule(body: MapIn, ctx: Ctx = Depends(require("inventory.import")), db: Session = Depends(get_db)):
    """Save a mapping rule (product text → stock item, source → godown, supplier → ledger). Applies to every file."""
    service.save_rule(db, ctx.company_id, body, ctx.user_id)
    db.flush()
    out = service.payload(db, ctx.company_id)
    db.commit()
    return out


@router.post("/purchases/override", response_model=dict[str, Any])
def override(body: OverrideIn, ctx: Ctx = Depends(require("inventory.import")), db: Session = Depends(get_db)):
    """Send one invoice to a different godown than its rule says (this upload only)."""
    service.set_override(db, ctx.company_id, body.id, body.godown)
    db.flush()
    out = service.payload(db, ctx.company_id)
    db.commit()
    return out


@router.post("/purchases/push", response_model=dict[str, Any])
def push(body: IdsIn, background: BackgroundTasks, ctx: Ctx = Depends(require("inventory.import")),
         db: Session = Depends(get_db)):
    """Create the Purchase vouchers in Tally. Invoices to the Direct godown also become Direct trucks.
    Godown stock is re-read from Tally afterwards."""
    results = _tally_errors(lambda: service.push(db, ctx.company_id, body.ids, ctx.user_id))
    db.commit()
    if any(r.get("ok") for r in results):
        snap, new = stock.start(db, ctx.company_id, "purchase_push", ctx.user_id)
        if new:
            background.add_task(stock.run, snap.id)
    out = service.payload(db, ctx.company_id, check_tally=True)
    db.commit()
    return {"results": results, **out}


@router.post("/purchases/forget", response_model=dict[str, Any])
def forget(body: ForgetIn, ctx: Ctx = Depends(require("inventory.import")), db: Session = Depends(get_db)):
    n = service.forget(db, ctx.company_id, body.ids, ctx.user_id)
    db.flush()
    out = service.payload(db, ctx.company_id, check_tally=True)
    db.commit()
    return {"forgotten": n, **out}


@router.get("/purchases/xml")
def voucher_xml(id: str = Query(max_length=120), ctx: Ctx = Depends(require("inventory.import")),
                db: Session = Depends(get_db)) -> dict:
    """Exactly what will be sent to Tally for this invoice."""
    return {"xml": service.invoice_xml(db, ctx.company_id, id)}


@router.post("/purchases/direct-truck", status_code=201)
def truck_from_invoice(body: InvoiceIdIn, ctx: Ctx = Depends(require("inventory.import")),
                       db: Session = Depends(get_db)) -> dict:
    """Add a Direct invoice that is already in Tally to the Direct trucks list."""
    t = service.add_truck_from_invoice(db, ctx.company_id, body.id, ctx.user_id)
    db.commit()
    return {"id": str(t.id), "truck_no": t.truck_no}


@router.get("/purchases/settings", response_model=dict[str, Any])
def get_settings(ctx: Ctx = Depends(require("inventory.import")), db: Session = Depends(get_db)):
    cfg = service.load_cfg(db, ctx.company_id)
    db.commit()
    return cfg


@router.put("/purchases/settings", response_model=dict[str, Any])
def put_settings(body: dict[str, Any], ctx: Ctx = Depends(require()), db: Session = Depends(get_db)):
    """Ledgers, voucher type and mapping rules (the app's Settings). Unknown keys are ignored."""
    _any(ctx, "tally.map", "admin.settings")
    row = service._profile_row(db, ctx.company_id)
    cfg = dict(row.config)
    for k, v in body.items():
        if k in service.PROFILE_KEYS:
            cfg[k] = v
    for sup in ("ambuja", "shree"):
        s = cfg.get("suppliers", {}).get(sup)
        if not isinstance(s, dict) or not isinstance(s.get("products"), list) or not isinstance(s.get("godowns"), list):
            raise AppError(422, "VALIDATION_ERROR", f"Settings for {sup} are incomplete")
    before = row.config
    row.config, row.updated_by = cfg, ctx.user_id
    service.forget_cache(ctx.company_id)
    write_audit(db, action="purchases.settings", entity_type="purchase_profiles", entity_id=row.id,
                actor_id=ctx.user_id, company_id=ctx.company_id, before=before, after=cfg)
    db.commit()
    return cfg


@router.post("/purchases/import-uploader")
def import_uploader(ctx: Ctx = Depends(require()), db: Session = Depends(get_db)) -> dict:
    """Take over the Purchase Uploader app's settings, rules and pushed-invoice log from this computer."""
    _any(ctx, "tally.map", "admin.settings")
    out = service.import_from_uploader(db, db.get(Company, ctx.company_id), ctx.user_id)
    db.commit()
    return out


# ---------------------------------------------------------------- stock
class StockRefreshOut(BaseModel):
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    summary: dict | None


class StockItemOut(BaseModel):
    tally_item: str
    product_id: uuid.UUID | None
    product_name: str | None
    qty: Decimal
    unit: str | None
    reserved_base: Decimal  # planned / loaded on trucks still at this godown, in the product's base unit
    base_uom: str | None


class StockGodownOut(BaseModel):
    godown_id: uuid.UUID | None
    name: str
    kind: str | None
    erp_enabled: bool
    items: list[StockItemOut]


class StockOut(BaseModel):
    as_of: datetime | None
    latest: StockRefreshOut | None
    godowns: list[StockGodownOut]


def _run_out(s) -> dict | None:
    return None if s is None else {"status": s.status, "started_at": s.started_at, "finished_at": s.finished_at,
                                   "error": s.error, "summary": s.summary}


@router.get("/stock", response_model=StockOut)
def godown_stock(ctx: Ctx = Depends(require()), db: Session = Depends(get_db)):
    """Stock in every godown as per Tally's last read."""
    _any(ctx, "inventory.view", "dispatch.view")
    stock.running(db, ctx.company_id)
    ok = stock.latest(db, ctx.company_id, ok_only=True)
    rows = stock.stock_rows(db, ctx.company_id)
    res = stock.reserved(db, ctx.company_id)
    godowns = {g.id: g for g in db.execute(select(Godown).where(Godown.company_id == ctx.company_id)).scalars()}
    products = {p.id: p for p in db.execute(select(Product).where(
        Product.id.in_({r.product_id for r in rows if r.product_id} or {None}))).scalars()}
    groups: dict = {}
    for r in sorted(rows, key=lambda r: (r.tally_godown.lower(), r.tally_item.lower())):
        g = godowns.get(r.godown_id) if r.godown_id else None
        if ctx.godown_ids is not None and (g is None or g.id not in ctx.godown_ids):
            continue
        key = r.godown_id or r.tally_godown
        grp = groups.setdefault(key, {"godown_id": r.godown_id, "name": g.name if g else r.tally_godown,
                                      "kind": g.kind if g else None, "erp_enabled": bool(g and g.erp_enabled),
                                      "items": []})
        p = products.get(r.product_id) if r.product_id else None
        grp["items"].append({"tally_item": r.tally_item, "product_id": r.product_id, "product_name": p.name if p else None,
                             "qty": r.qty, "unit": r.tally_unit,
                             "reserved_base": res.get((r.godown_id, r.product_id), Decimal(0)) if p else Decimal(0),
                             "base_uom": p.base_uom if p else None})
    out = sorted(groups.values(), key=lambda g: (not g["erp_enabled"], g["name"].lower()))
    return {"as_of": ok.finished_at if ok else None, "latest": _run_out(stock.latest(db, ctx.company_id)),
            "godowns": out}


@router.post("/stock/refresh", response_model=StockRefreshOut, status_code=202)
def refresh_stock(background: BackgroundTasks, ctx: Ctx = Depends(require()), db: Session = Depends(get_db)):
    """Read godown stock from Tally again (in the background; poll GET /stock)."""
    _any(ctx, "inventory.view", "dispatch.plan", "tally.sync")
    snap, new = stock.start(db, ctx.company_id, "button", ctx.user_id)
    if new:
        background.add_task(stock.run, snap.id)
    return _run_out(snap)


# ---------------------------------------------------------------- direct trucks
class TruckLineOut(BaseModel):
    tally_item: str
    product_id: uuid.UUID | None
    product_name: str | None
    qty: Decimal
    uom: str
    left_qty: Decimal | None  # in `uom`


class TruckDeliveryOut(BaseModel):
    do_id: uuid.UUID
    do_no: str
    status: str
    dealer_name: str


class DirectTruckOut(BaseModel):
    id: uuid.UUID
    truck_no: str
    supplier: str
    invoice_no: str
    invoice_date: date
    source: str | None
    ref: str | None
    status: str
    close_note: str | None
    godown_name: str
    lines: list[TruckLineOut]
    deliveries: list[TruckDeliveryOut]


class CloseIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


def _trucks_out(db: Session, trucks: list[DirectTruck]) -> list[dict]:
    if not trucks:
        return []
    tl = stock.truck_lines(db, trucks)
    names = {p.id: p.name for p in db.execute(select(Product).where(Product.id.in_(
        {it.product_id for t in trucks for it in t.items if it.product_id} or {None}))).scalars()}
    godowns = {g.id: g.name for g in db.execute(select(Godown).where(Godown.id.in_({t.godown_id for t in trucks}))).scalars()}
    dos = db.execute(select(DeliveryOrder, Dealer.name).join(Dealer, Dealer.id == DeliveryOrder.dealer_id)
                     .where(DeliveryOrder.direct_truck_id.in_([t.id for t in trucks]))
                     .order_by(DeliveryOrder.created_at)).all()
    out = []
    for t in trucks:
        lines = []
        for ln in tl[t.id]:
            left = None
            if ln["left_base"] is not None and ln["base_qty"]:
                left = (ln["qty"] * ln["left_base"] / ln["base_qty"]).quantize(Decimal("0.001"))
            lines.append({"tally_item": ln["tally_item"], "product_id": ln["product_id"],
                          "product_name": names.get(ln["product_id"]), "qty": ln["qty"], "uom": ln["uom"],
                          "left_qty": left})
        out.append({"id": t.id, "truck_no": t.truck_no, "supplier": t.supplier, "invoice_no": t.invoice_no,
                    "invoice_date": t.invoice_date, "source": t.source, "ref": t.ref, "status": t.status,
                    "close_note": t.close_note, "godown_name": godowns.get(t.godown_id, ""), "lines": lines,
                    "deliveries": [{"do_id": d.id, "do_no": d.do_no, "status": d.status, "dealer_name": n}
                                   for d, n in dos if d.direct_truck_id == t.id]})
    return out


@router.get("/direct-trucks", response_model=list[DirectTruckOut])
def direct_trucks(status: Literal["OPEN", "CLOSED", "ALL"] = "OPEN", ctx: Ctx = Depends(require()),
                  db: Session = Depends(get_db)):
    """Factory trucks that came to the Direct godown, with what is still on each."""
    _any(ctx, "dispatch.view", "inventory.view")
    stmt = select(DirectTruck).where(DirectTruck.company_id == ctx.company_id)
    if status != "ALL":
        stmt = stmt.where(DirectTruck.status == status)
    trucks = list(db.execute(stmt.order_by(DirectTruck.invoice_date.desc(), DirectTruck.created_at.desc())
                             .limit(200)).scalars())
    return _trucks_out(db, trucks)


def _truck(db: Session, ctx: Ctx, truck_id: uuid.UUID) -> DirectTruck:
    t = db.get(DirectTruck, truck_id)
    if t is None or t.company_id != ctx.company_id:
        raise AppError(404, "NOT_FOUND", "Direct truck not found")
    return t


@router.post("/direct-trucks/{truck_id}/close", response_model=DirectTruckOut)
def close_truck(truck_id: uuid.UUID, body: CloseIn, ctx: Ctx = Depends(require("dispatch.plan")),
                db: Session = Depends(get_db)):
    """Take a truck off the list, e.g. when its load was delivered without the ERP."""
    t = _truck(db, ctx, truck_id)
    t.status, t.close_note, t.updated_by = "CLOSED", body.note, ctx.user_id
    write_audit(db, action="direct_truck.closed", entity_type="direct_trucks", entity_id=t.id, actor_id=ctx.user_id,
                company_id=ctx.company_id, after={"note": body.note})
    db.commit()
    return _trucks_out(db, [t])[0]


@router.post("/direct-trucks/{truck_id}/reopen", response_model=DirectTruckOut)
def reopen_truck(truck_id: uuid.UUID, ctx: Ctx = Depends(require("dispatch.plan")), db: Session = Depends(get_db)):
    t = _truck(db, ctx, truck_id)
    t.status, t.close_note, t.updated_by = "OPEN", None, ctx.user_id
    write_audit(db, action="direct_truck.reopened", entity_type="direct_trucks", entity_id=t.id,
                actor_id=ctx.user_id, company_id=ctx.company_id)
    db.commit()
    return _trucks_out(db, [t])[0]
