"""Sync with TallyPrime: customers (with balances and mobile numbers), godowns and stock items, then godown stock,
then delivered orders sent to Tally and their approvals read back.

Used by the web "Sync with Tally" button (runs in the background, one run at a time per company) and by the
`sync-tally-masters` command. Full sync: anything that disappeared from Tally is flagged, never deleted.
"""

import re
import traceback
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.db import SessionLocal
from app.modules.integrations.tally.models import TallySyncRun
from app.modules.integrations.tally.xml_client import TallyClient, TallyError
from app.modules.masters.models import Company
from app.modules.masters.service import (
    mark_missing_dealers,
    upsert_dealers_from_tally,
    upsert_godowns_from_tally,
)
from app.modules.masters.tally_products import upsert_products_from_tally
from app.modules.platform.models import SystemSetting

STALE_RUN = timedelta(minutes=15)  # a RUNNING row older than this is treated as dead (server restarted mid-sync)


def match_company_name(wanted: str, loaded: list[str]) -> str | None:
    """Tally company names often carry a trailing full stop or extra spaces ("Ishika Traders Daily.").
    Match ignoring case, surrounding punctuation and repeated spaces; return Tally's exact name."""

    def norm(x: str) -> str:
        return re.sub(r"\s+", " ", x).strip().strip(".,").strip().lower()

    if wanted in loaded:
        return wanted
    hits = [name for name in loaded if norm(name) == norm(wanted)]
    return hits[0] if len(hits) == 1 else None


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not re.match(r"^https?://", url):
        url = "http://" + url
    if not re.search(r":\d+$", url):
        url += ":9000"  # TallyPrime's default HTTP port
    return url


def fetch(client: TallyClient, company: Company) -> tuple[str, list, list, list]:
    loaded = client.companies()
    tally_name = match_company_name(company.tally_company_name or company.legal_name, loaded)
    if tally_name is None:
        raise TallyError(f"Company '{company.tally_company_name or company.legal_name}' is not open in Tally. "
                         f"Open companies: {', '.join(loaded) or 'none'}")
    return tally_name, client.ledgers(tally_name), client.godowns(tally_name), client.stock_items(tally_name)


def apply(db: Session, company: Company, tally_name: str, ledgers, godowns, items, actor_id) -> dict:
    company.tally_company_name = tally_name  # requests must use Tally's exact spelling
    d = upsert_dealers_from_tally(db, company.id, ledgers, actor_id=actor_id)
    d["missing"] = mark_missing_dealers(db, company.id, {lg.guid for lg in ledgers}, actor_id=actor_id) if ledgers else 0
    g = upsert_godowns_from_tally(db, company.id, godowns, actor_id=actor_id, full=bool(godowns))
    p = upsert_products_from_tally(db, company.id, items, actor_id=actor_id, full=bool(items))
    return {"tally": {"ledgers": len(ledgers), "godowns": len(godowns), "stock_items": len(items)},
            "dealers": d, "godowns": g, "products": p}


def running_run(db: Session, company_id: uuid.UUID) -> TallySyncRun | None:
    run = db.execute(select(TallySyncRun).where(TallySyncRun.company_id == company_id,
                                                TallySyncRun.status == "RUNNING")
                     .order_by(TallySyncRun.started_at.desc())).scalars().first()
    if run and datetime.now(UTC) - run.started_at > STALE_RUN:
        run.status, run.finished_at, run.error = "FAILED", datetime.now(UTC), "Stopped: the server restarted during the sync"
        db.commit()
        return None
    return run


def save_url(db: Session, company_id: uuid.UUID, url: str, actor_id) -> None:
    row = db.execute(select(SystemSetting).where(SystemSetting.company_id == company_id,
                                                 SystemSetting.key == "tally.url")).scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(company_id=company_id, key="tally.url", value={"v": url}, updated_by=actor_id))
    elif row.value.get("v") != url:
        row.value, row.updated_by = {"v": url}, actor_id


STEPS = [  # what one "Sync with Tally" does, in this order
    ("masters", "Dealers & balances, godowns, products"),
    ("stock", "Godown stock"),
    ("sales_push", "Delivered orders sent to Tally"),
    ("sales_check", "Approvals of sales in Tally"),
]


def _step(db: Session, run: TallySyncRun, key: str, status: str, detail: str = "") -> None:
    """Record one step's progress on the run (committed, so the web card can show it while the sync runs)."""
    summary = dict(run.summary or {})
    steps = [dict(x) for x in summary.get("steps") or [{"key": k, "label": lbl, "status": "WAITING", "detail": ""}
                                                      for k, lbl in STEPS]]
    for x in steps:
        if x["key"] == key:
            x["status"], x["detail"] = status, detail
    summary["steps"] = steps
    run.summary = summary
    db.commit()


def _sync_stock(db: Session, run: TallySyncRun) -> tuple[str, str]:
    import time

    from app.modules.purchases import stock
    from app.modules.purchases.models import StockSnapshot

    snap, new = stock.start(db, run.company_id, "tally_sync", run.triggered_by)
    if new:
        stock.run(snap.id)
    else:  # someone pressed Refresh a moment ago: wait for that read instead of starting another
        for _ in range(300):
            db.expire_all()
            if db.get(StockSnapshot, snap.id).status != "RUNNING":
                break
            time.sleep(1)
    db.expire_all()
    snap = db.get(StockSnapshot, snap.id)
    if snap.status == "SUCCESS":
        return "OK", f"{(snap.summary or {}).get('rows', 0)} godown-item lines"
    return "FAILED", snap.error or "Could not read stock"


def _sales_push(db: Session, company_id: uuid.UUID, actor_id) -> tuple[str, str]:
    from app.modules.integrations.tally import sales
    from app.modules.integrations.tally.models import TallySale

    ids = list(db.execute(select(TallySale.id).where(TallySale.company_id == company_id,
                                                     TallySale.status.in_(("PENDING", "FAILED")))).scalars())
    if not ids:
        return "OK", "Nothing to send"
    r = sales.push_pending(db, company_id, actor_id, ids)
    if r.get("error"):
        return "FAILED", str(r["error"])
    return ("FAILED" if r["failed"] else "OK"), f"{r['pushed']} sent" + (f", {r['failed']} failed" if r["failed"] else "")


def _sales_check(db: Session, company_id: uuid.UUID) -> tuple[str, str]:
    from app.modules.integrations.tally import sales

    r = sales.check(db, company_id)
    if not r.get("checked"):
        return "OK", "Nothing waiting"
    return "OK", f"{r['approved']} approved, {r['rejected']} rejected, {r['waiting']} still waiting"


def run_in_background(run_id: uuid.UUID) -> None:
    """Executed after the HTTP response has been sent; uses its own database session.

    One sync brings everything up to date: dealers (balances, mobile numbers), godowns and products, then godown
    stock, then sends delivered orders still to go to Tally and reads back which ones the admin approved. If Tally
    cannot be reached at the first step the rest is skipped; a later step failing does not undo the earlier ones."""
    db = SessionLocal()
    try:
        run = db.get(TallySyncRun, run_id)
        company = db.get(Company, run.company_id)
        _step(db, run, "masters", "RUNNING")
        try:
            tally_name, ledgers, godowns, items = fetch(TallyClient(run.tally_url), company)
            summary = apply(db, company, tally_name, ledgers, godowns, items, run.triggered_by)
            save_url(db, company.id, run.tally_url, run.triggered_by)
            summary["steps"] = (run.summary or {}).get("steps")
            run.summary = summary
            _step(db, run, "masters", "OK", f"{len(ledgers)} customers, {len(godowns)} godowns, {len(items)} items")
            write_audit(db, action="tally.sync", entity_type="tally_sync_runs", entity_id=run.id,
                        actor_id=run.triggered_by, company_id=company.id, after=summary["tally"])
            db.commit()
        except Exception as e:  # noqa: BLE001 - never leave a run stuck in RUNNING
            db.rollback()
            run = db.get(TallySyncRun, run_id)
            run.status = "FAILED"
            run.error = str(e) if isinstance(e, TallyError) else f"Unexpected error: {e}"
            if not isinstance(e, TallyError):
                traceback.print_exc()
            _step(db, run, "masters", "FAILED", run.error)
            for key, _ in STEPS[1:]:
                _step(db, run, key, "SKIPPED", "Not done because the first step failed")
            run.finished_at = datetime.now(UTC)
            db.commit()
            return

        company_id, actor_id = run.company_id, run.triggered_by
        for key, fn in (("stock", lambda: _sync_stock(db, db.get(TallySyncRun, run_id))),
                        ("sales_push", lambda: _sales_push(db, company_id, actor_id)),
                        ("sales_check", lambda: _sales_check(db, company_id))):
            _step(db, run, key, "RUNNING")
            try:
                status, detail = fn()
            except Exception as e:  # noqa: BLE001 - one step failing must not stop the others
                db.rollback()
                status, detail = "FAILED", str(e)
                traceback.print_exc()
            run = db.get(TallySyncRun, run_id)
            _step(db, run, key, status, detail)

        run.status = "SUCCESS"
        bad = [x["label"] for x in run.summary["steps"] if x["status"] == "FAILED"]
        run.error = ("Done, but these did not work: " + "; ".join(bad)) if bad else None
        run.finished_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()
