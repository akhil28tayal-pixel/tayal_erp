import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, require
from app.core.errors import AppError
from app.modules.identity.models import User
from app.modules.integrations.tally import sync
from app.modules.integrations.tally.models import TallySyncRun
from app.modules.platform.settings import get_setting

router = APIRouter(prefix="/tally", tags=["tally"])


class SyncIn(BaseModel):
    tally_url: str | None = Field(default=None, max_length=200,
                                  description="Address of the Tally PC, e.g. 192.168.0.5:9000. Saved after a good sync.")


class RunOut(BaseModel):
    id: uuid.UUID
    status: str
    tally_url: str
    started_at: datetime
    finished_at: datetime | None
    triggered_by_name: str | None
    summary: dict | None
    error: str | None


class StatusOut(BaseModel):
    tally_url: str
    latest: RunOut | None
    last_success: RunOut | None


def _out(db: Session, run: TallySyncRun | None) -> dict | None:
    if run is None:
        return None
    name = db.get(User, run.triggered_by).full_name if run.triggered_by else "Automatic"
    return {"id": run.id, "status": run.status, "tally_url": run.tally_url, "started_at": run.started_at,
            "finished_at": run.finished_at, "triggered_by_name": name, "summary": run.summary, "error": run.error}


@router.get("/sync", response_model=StatusOut)
def status(ctx: Ctx = Depends(require("tally.view")), db: Session = Depends(get_db)):
    sync.running_run(db, ctx.company_id)  # clears runs left RUNNING by a restart
    base = select(TallySyncRun).where(TallySyncRun.company_id == ctx.company_id).order_by(TallySyncRun.started_at.desc())
    latest = db.execute(base).scalars().first()
    ok = db.execute(base.where(TallySyncRun.status == "SUCCESS")).scalars().first()
    return {"tally_url": get_setting(db, ctx.company_id, "tally.url"), "latest": _out(db, latest),
            "last_success": _out(db, ok)}


@router.post("/sync", response_model=RunOut, status_code=202)
def start(body: SyncIn, background: BackgroundTasks, ctx: Ctx = Depends(require("tally.sync")),
          db: Session = Depends(get_db)):
    """Start a sync of customers, balances, mobile numbers, godowns and stock items. Poll GET /tally/sync."""
    url = body.tally_url or get_setting(db, ctx.company_id, "tally.url")
    if not url:
        raise AppError(422, "TALLY_URL_REQUIRED", "Enter the address of the Tally computer, e.g. 192.168.0.5:9000")
    if sync.running_run(db, ctx.company_id):
        raise AppError(409, "SYNC_RUNNING", "A Tally sync is already running")
    run = TallySyncRun(company_id=ctx.company_id, tally_url=sync.normalize_url(url), status="RUNNING",
                       started_at=datetime.now(UTC), triggered_by=ctx.user_id)
    db.add(run)
    db.commit()
    background.add_task(sync.run_in_background, run.id)
    return _out(db, run)


# ---------------------------------------------------------------- delivered orders → Tally (admin approves there)
class TallySaleOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_no: str | None
    dealer_name: str
    status: str
    amount: str | None
    voucher_date: datetime | None
    tally_voucher_no: str | None
    error: str | None
    attempts: int
    queued_at: datetime
    pushed_at: datetime | None
    checked_at: datetime | None
    decided_at: datetime | None


class TallySalesOut(BaseModel):
    sales_ledger: str
    voucher_type: str
    counts: dict[str, int]
    items: list[TallySaleOut]


class IdsIn(BaseModel):
    ids: list[uuid.UUID] | None = Field(default=None, description="None = everything pending or failed")


@router.get("/sales", response_model=TallySalesOut)
def tally_sales_list(status: str | None = None, ctx: Ctx = Depends(require("tally.view")),
                     db: Session = Depends(get_db)):
    """Delivered orders sent to Tally as Optional vouchers, and whether the admin approved them in Tally."""
    from sqlalchemy import func

    from app.modules.integrations.tally.models import TallySale
    from app.modules.masters.models import Dealer
    from app.modules.orders.models import SalesOrder

    stmt = (select(TallySale, SalesOrder.order_no, Dealer.name).join(SalesOrder, SalesOrder.id == TallySale.order_id)
            .join(Dealer, Dealer.id == SalesOrder.dealer_id).where(TallySale.company_id == ctx.company_id))
    if status:
        stmt = stmt.where(TallySale.status == status)
    rows = db.execute(stmt.order_by(TallySale.queued_at.desc()).limit(300)).all()
    counts = dict(db.execute(select(TallySale.status, func.count()).where(TallySale.company_id == ctx.company_id)
                             .group_by(TallySale.status)).all())
    return {"sales_ledger": get_setting(db, ctx.company_id, "tally.sales_ledger"),
            "voucher_type": get_setting(db, ctx.company_id, "tally.sales_voucher_type"), "counts": counts,
            "items": [{"id": s.id, "order_id": s.order_id, "order_no": no, "dealer_name": dn, "status": s.status,
                       "amount": s.amount, "voucher_date": s.voucher_date, "tally_voucher_no": s.tally_voucher_no,
                       "error": s.error, "attempts": s.attempts, "queued_at": s.queued_at, "pushed_at": s.pushed_at,
                       "checked_at": s.checked_at, "decided_at": s.decided_at} for s, no, dn in rows]}


@router.post("/sales/push")
def tally_sales_push(body: IdsIn, ctx: Ctx = Depends(require("tally.sync")), db: Session = Depends(get_db)) -> dict:
    """Send pending orders now, or retry the ones given (failed / rejected ones go again as new Optional vouchers)."""
    from app.modules.integrations.tally import sales
    from app.modules.integrations.tally.models import TallySale

    ids = body.ids
    if ids:
        for s in db.execute(select(TallySale).where(TallySale.company_id == ctx.company_id,
                                                    TallySale.id.in_(ids))).scalars():
            if s.status in ("FAILED", "REJECTED"):
                s.status, s.error = "PENDING", None
        db.commit()
    return sales.push_pending(db, ctx.company_id, ctx.user_id, ids)


@router.post("/sales/check")
def tally_sales_check(ctx: Ctx = Depends(require("tally.view")), db: Session = Depends(get_db)) -> dict:
    """Read Tally: which waiting vouchers the admin has approved (made Regular) or deleted."""
    from app.modules.integrations.tally import sales
    from app.modules.purchases.tally_io import TallyError

    try:
        return sales.check(db, ctx.company_id)
    except TallyError as e:
        raise AppError(502, "TALLY_ERROR", str(e)) from e


@router.get("/sales/{sale_id}/xml")
def tally_sale_xml(sale_id: uuid.UUID, ctx: Ctx = Depends(require("tally.view")), db: Session = Depends(get_db)) -> dict:
    from app.modules.integrations.tally.models import TallySale

    s = db.get(TallySale, sale_id)
    if s is None or s.company_id != ctx.company_id:
        raise AppError(404, "NOT_FOUND", "Not found")
    return {"xml": s.xml or ""}


@router.get("/sales-ledgers")
def tally_sales_ledgers(ctx: Ctx = Depends(require("tally.view")), db: Session = Depends(get_db)) -> dict:
    """Ledgers under Sales Accounts in Tally, to choose the one delivered orders are booked to."""
    from app.modules.integrations.tally import sales
    from app.modules.purchases.tally_io import TallyError

    try:
        m = sales._client(db, ctx.company_id).masters()
    except TallyError as e:
        raise AppError(502, "TALLY_ERROR", str(e)) from e
    return {"ledgers": [x["name"] for x in m["ledgers"] if x["parent"].lower() == "sales accounts"]}
