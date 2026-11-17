"""Send the balance reminder to every dealer who owes and has a mobile number (Cloud API only).

The preview and the run use the same selection, so what the user confirms is what gets sent."""

import time
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.modules.collections import service, whatsapp_cloud
from app.modules.collections.models import WhatsAppBulkRun
from app.modules.masters.models import Dealer
from app.modules.platform.settings import get_setting

STALE_RUN = timedelta(minutes=30)


def select_dealers(db: Session, company_id: uuid.UUID, visible, min_balance: Decimal, skip_recent: bool,
                   include_blocked: bool) -> dict:
    """Split dealers with dues into: to send, no mobile, recently reminded, blocked."""
    stmt = visible(select(Dealer).where(Dealer.company_id == company_id, Dealer.tally_deleted.is_(False),
                                        Dealer.tally_balance > 0, Dealer.tally_balance >= min_balance))
    dealers = db.execute(stmt.order_by(Dealer.tally_balance.desc())).scalars().all()
    gap = timedelta(hours=int(get_setting(db, company_id, "whatsapp.reminder_gap_hours")))
    last = service.last_reminders(db, [d.id for d in dealers])
    now = datetime.now(UTC)
    out = {"send": [], "no_mobile": [], "recent": [], "blocked": []}
    for d in dealers:
        if d.status != "ACTIVE" and not include_blocked:
            out["blocked"].append(d)
        elif not service.whatsapp_numbers(db, d):
            out["no_mobile"].append(d)
        elif skip_recent and d.id in last and now - last[d.id].created_at < gap:
            out["recent"].append(d)
        else:
            out["send"].append(d)
    return out


def running(db: Session, company_id: uuid.UUID) -> WhatsAppBulkRun | None:
    run = db.execute(select(WhatsAppBulkRun).where(WhatsAppBulkRun.company_id == company_id,
                                                   WhatsAppBulkRun.status == "RUNNING")).scalars().first()
    if run and datetime.now(UTC) - run.started_at > STALE_RUN:
        run.status, run.finished_at, run.error = "FAILED", datetime.now(UTC), "Stopped: the server restarted"
        db.commit()
        return None
    return run


def execute(run_id: uuid.UUID, dealer_ids: list[uuid.UUID]) -> None:
    """Background task with its own session. Each message is committed as it goes, so progress is visible."""
    db = SessionLocal()
    try:
        run = db.get(WhatsAppBulkRun, run_id)
        for did in dealer_ids:
            dealer = db.get(Dealer, did)
            try:
                service.prepare_balance_message(db, run.company_id, dealer, run.started_by, None, confirm_repeat=True)
                db.commit()
                run.sent += 1
            except AppError as e:  # failed sends are already logged per dealer by prepare_balance_message
                db.rollback()
                run = db.get(WhatsAppBulkRun, run_id)
                run.failed += 1
                run.failures = [*run.failures, {"dealer": dealer.name, "error": e.detail}][:200]
            db.commit()
            time.sleep(0.05)  # stay well inside Meta's per-second limits
        run.status, run.finished_at = "DONE", datetime.now(UTC)
        db.commit()
    except Exception as e:
        traceback.print_exc()
        db.rollback()
        run = db.get(WhatsAppBulkRun, run_id)
        run.status, run.finished_at, run.error = "FAILED", datetime.now(UTC), f"Unexpected error: {e}"
        db.commit()
    finally:
        db.close()


def check_ready(db: Session, company_id: uuid.UUID) -> None:
    if get_setting(db, company_id, "whatsapp.provider") != "CLOUD_API" or not whatsapp_cloud.configured():
        raise AppError(422, "AUTOMATIC_SENDING_OFF", "Sending to everyone needs automatic sending. Turn on "
                       "Settings -> WhatsApp messages -> Automatic (Meta API) first.")
