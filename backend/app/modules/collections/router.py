import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import Ctx, require
from app.core.errors import AppError, not_found
from app.core.pagination import Page, PageParams, paginate
from app.modules.collections import bulk, service, whatsapp_cloud
from app.modules.collections.models import WhatsAppBulkRun
from app.modules.identity.models import User
from app.modules.masters.models import Company, Dealer
from app.modules.masters.service import assigned_dealer_ids_subquery, dealer_search
from app.modules.platform.settings import get_setting

router = APIRouter(tags=["collections"])


class Number(BaseModel):
    number: str
    label: str


class BalanceRow(BaseModel):
    dealer_id: uuid.UUID
    code: str
    name: str
    status: str
    tally_balance: Decimal | None
    balance_as_of: datetime | None
    numbers: list[Number]
    last_reminder_at: datetime | None
    last_reminder_by: str | None
    last_reminder_balance: Decimal | None


class BalanceSummary(BaseModel):
    whatsapp_provider: str
    dealers_with_dues: int
    total_due: Decimal
    advances: Decimal
    last_sync: datetime | None


class SendIn(BaseModel):
    number: str | None = Field(default=None, max_length=20, description="Default: the dealer's best mobile")
    confirm_repeat: bool = False


class SendOut(BaseModel):
    message_id: uuid.UUID
    number: str
    message: str
    provider: str
    url: str | None = Field(description="CLICK_TO_CHAT: open this to send. CLOUD_API: null, already sent.")


class WhatsAppStatus(BaseModel):
    provider: str
    api_keys_set: bool
    phone_number_id: str | None
    api_version: str
    template_name: str
    template_language: str
    delivery_template: str = ""


class TestIn(BaseModel):
    number: str = Field(max_length=20)


class TestOut(BaseModel):
    number: str
    provider_message_id: str


def _visible(stmt, ctx: Ctx):
    if ctx.has("collections.view_all") or ctx.has("dealers.view_all"):
        return stmt
    return stmt.where(Dealer.id.in_(assigned_dealer_ids_subquery(ctx.user.employee_id)))


@router.get("/collections/summary", response_model=BalanceSummary)
def summary(ctx: Ctx = Depends(require("collections.view")), db: Session = Depends(get_db)):
    base = _visible(select(Dealer).where(Dealer.company_id == ctx.company_id, Dealer.tally_deleted.is_(False)), ctx)
    sub = base.subquery()
    row = db.execute(select(
        func.count().filter(sub.c.tally_balance > 0),
        func.coalesce(func.sum(sub.c.tally_balance).filter(sub.c.tally_balance > 0), 0),
        func.coalesce(-func.sum(sub.c.tally_balance).filter(sub.c.tally_balance < 0), 0),
        func.max(sub.c.tally_balance_at),
    )).one()
    return {"whatsapp_provider": get_setting(db, ctx.company_id, "whatsapp.provider"), "dealers_with_dues": row[0], "total_due": row[1], "advances": row[2], "last_sync": row[3]}


@router.get("/collections/balances", response_model=Page[BalanceRow])
def balances(params: PageParams = Depends(), q: str | None = None, dues_only: bool = True,
             ctx: Ctx = Depends(require("collections.view")), db: Session = Depends(get_db)):
    """Dealers with their Tally balance, mobile numbers and when they were last reminded — biggest dues first."""
    stmt = _visible(select(Dealer).where(Dealer.company_id == ctx.company_id, Dealer.tally_deleted.is_(False)), ctx)
    if dues_only:
        stmt = stmt.where(Dealer.tally_balance > 0)
    if q:
        stmt = stmt.where(dealer_search(q))
    page = paginate(db, stmt, params, {"name": Dealer.name, "balance": Dealer.tally_balance},
                    [Dealer.tally_balance.desc().nulls_last(), Dealer.name])
    dealers = page["items"]
    last = service.last_reminders(db, [d.id for d in dealers])
    users = {u.id: u.full_name for u in db.execute(
        select(User).where(User.id.in_({m.created_by for m in last.values() if m.created_by}))).scalars()} if last else {}
    items = []
    for d in dealers:
        m = last.get(d.id)
        items.append({"dealer_id": d.id, "code": d.code, "name": d.name, "status": d.status,
                      "tally_balance": d.tally_balance, "balance_as_of": d.tally_balance_at,
                      "numbers": service.whatsapp_numbers(db, d),
                      "last_reminder_at": m.created_at if m else None,
                      "last_reminder_by": users.get(m.created_by) if m else None,
                      "last_reminder_balance": m.balance if m else None})
    return {**page, "items": items}


@router.post("/dealers/{dealer_id}/whatsapp/balance", response_model=SendOut)
def whatsapp_balance(dealer_id: uuid.UUID, body: SendIn, ctx: Ctx = Depends(require("collections.followup")),
                     db: Session = Depends(get_db)):
    """Build the balance message and a WhatsApp link that opens it ready to send. Logged per dealer."""
    dealer = db.execute(_visible(select(Dealer).where(Dealer.id == dealer_id,
                                                      Dealer.company_id == ctx.company_id), ctx)).scalar_one_or_none()
    if dealer is None:
        raise not_found("Dealer")
    msg, url = service.prepare_balance_message(db, ctx.company_id, dealer, ctx.user_id, body.number,
                                               body.confirm_repeat)
    db.commit()
    return {"message_id": msg.id, "number": msg.phone, "message": msg.body, "provider": msg.provider, "url": url}


@router.get("/collections/whatsapp", response_model=WhatsAppStatus)
def whatsapp_status(ctx: Ctx = Depends(require("collections.view")), db: Session = Depends(get_db)):
    s = get_settings()
    pid = s.whatsapp_phone_number_id
    return {"provider": get_setting(db, ctx.company_id, "whatsapp.provider"),
            "api_keys_set": whatsapp_cloud.configured(),
            "phone_number_id": f"…{pid[-4:]}" if pid else None, "api_version": s.whatsapp_api_version,
            "template_name": get_setting(db, ctx.company_id, "whatsapp.template_name"),
            "template_language": get_setting(db, ctx.company_id, "whatsapp.template_language"),
            "delivery_template": get_setting(db, ctx.company_id, "whatsapp.delivery_template")}


@router.post("/collections/whatsapp/test", response_model=TestOut)
def whatsapp_test(body: TestIn, ctx: Ctx = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    """Send the balance template with sample values to one number, to check the API keys and the template."""
    number = service.normalize_mobile(body.number)
    if number is None:
        raise AppError(422, "INVALID_MOBILE", "Enter a 10-digit mobile number")
    company = db.get(Company, ctx.company_id)
    params = service.template_params(db, ctx.company_id, {
        "dealer": "Test Dealer", "balance": "1,23,456", "as_of": datetime.now(service._IST).strftime("%d %b %Y"),
        "sender": service.sender_name(db, company)})
    try:
        mid = whatsapp_cloud.send_template(number, get_setting(db, ctx.company_id, "whatsapp.template_name"),
                                           get_setting(db, ctx.company_id, "whatsapp.template_language"), params)
    except whatsapp_cloud.WhatsAppError as e:
        raise AppError(502, "WHATSAPP_SEND_FAILED", str(e)) from e
    return {"number": number, "provider_message_id": mid}


# ------------------------------------------------------------------ send to everyone
class BulkIn(BaseModel):
    min_balance: Decimal = Field(default=Decimal("0"), ge=0, description="Skip dealers who owe less than this")
    skip_recent: bool = Field(default=True, description="Skip dealers reminded within whatsapp.reminder_gap_hours")
    include_blocked: bool = False


class BulkDealer(BaseModel):
    dealer_id: uuid.UUID
    name: str
    balance: Decimal


class BulkPreview(BaseModel):
    to_send: int
    to_send_amount: Decimal
    no_mobile: int
    recently_reminded: int
    blocked: int
    sample: list[BulkDealer]


class BulkRunOut(BaseModel):
    id: uuid.UUID
    status: str
    total: int
    sent: int
    failed: int
    started_at: datetime
    finished_at: datetime | None
    started_by_name: str | None
    failures: list[dict]
    error: str | None


def _run_out(db: Session, run) -> dict | None:
    if run is None:
        return None
    name = db.get(User, run.started_by).full_name if run.started_by else None
    return {"id": run.id, "status": run.status, "total": run.total, "sent": run.sent, "failed": run.failed,
            "started_at": run.started_at, "finished_at": run.finished_at, "started_by_name": name,
            "failures": run.failures, "error": run.error}


@router.post("/collections/whatsapp/bulk/preview", response_model=BulkPreview)
def bulk_preview(body: BulkIn, ctx: Ctx = Depends(require("collections.followup")), db: Session = Depends(get_db)):
    """Who would get the balance message if "Send to all" is pressed now."""
    sel = bulk.select_dealers(db, ctx.company_id, lambda st: _visible(st, ctx), body.min_balance, body.skip_recent,
                              body.include_blocked)
    return {"to_send": len(sel["send"]), "to_send_amount": sum((d.tally_balance for d in sel["send"]), Decimal(0)),
            "no_mobile": len(sel["no_mobile"]), "recently_reminded": len(sel["recent"]), "blocked": len(sel["blocked"]),
            "sample": [{"dealer_id": d.id, "name": d.name, "balance": d.tally_balance} for d in sel["send"][:8]]}


@router.post("/collections/whatsapp/bulk", response_model=BulkRunOut, status_code=202)
def bulk_start(body: BulkIn, background: BackgroundTasks, ctx: Ctx = Depends(require("collections.followup")),
               db: Session = Depends(get_db)):
    """Send the balance reminder to everyone selected by the same rules as the preview. Poll GET .../bulk."""
    bulk.check_ready(db, ctx.company_id)
    if bulk.running(db, ctx.company_id):
        raise AppError(409, "BULK_RUNNING", "Messages are already being sent. Wait for that run to finish.")
    sel = bulk.select_dealers(db, ctx.company_id, lambda st: _visible(st, ctx), body.min_balance, body.skip_recent,
                              body.include_blocked)
    if not sel["send"]:
        raise AppError(422, "NOTHING_TO_SEND", "No dealer to send to with these options.")
    run = WhatsAppBulkRun(company_id=ctx.company_id, status="RUNNING", options=body.model_dump(mode="json"),
                          total=len(sel["send"]), started_by=ctx.user_id, started_at=datetime.now(UTC), failures=[])
    db.add(run)
    write_audit(db, action="whatsapp.bulk_balance", entity_type="whatsapp_bulk_runs", entity_id=run.id,
                actor_id=ctx.user_id, company_id=ctx.company_id, after={"total": run.total, **run.options})
    db.commit()
    background.add_task(bulk.execute, run.id, [d.id for d in sel["send"]])
    return _run_out(db, run)


@router.get("/collections/whatsapp/bulk", response_model=BulkRunOut | None)
def bulk_latest(ctx: Ctx = Depends(require("collections.view")), db: Session = Depends(get_db)):
    """The latest "send to all" run (live progress while it is RUNNING)."""
    bulk.running(db, ctx.company_id)
    run = db.execute(select(WhatsAppBulkRun).where(WhatsAppBulkRun.company_id == ctx.company_id)
                     .order_by(WhatsAppBulkRun.started_at.desc())).scalars().first()
    return _run_out(db, run)
