"""Dealer statement: the dealer's ledger in Tally for a period — opening balance, every voucher with debit / credit
and running balance, closing balance. Read straight from Tally (one ledger, one date range, so it stays light)."""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.integrations.tally.xml_client import parse_balance
from app.modules.masters.models import Dealer

MAX_DAYS = 366


def _q(v: str) -> str:
    return '"' + v.replace('"', "") + '"'


def statement(db: Session, company_id: uuid.UUID, dealer: Dealer, d_from: dt.date, d_to: dt.date) -> dict:
    from app.modules.integrations.tally.sales import _client
    from app.modules.purchases.tally_io import TallyError

    if d_to < d_from:
        raise AppError(422, "VALIDATION_ERROR", "The end date is before the start date")
    if (d_to - d_from).days >= MAX_DAYS:
        raise AppError(422, "VALIDATION_ERROR", "Choose at most one year")
    ledger = (dealer.tally_ledger_name or dealer.name).strip()
    try:
        c = _client(db, company_id)
        # balance at the end of the day before the period = opening balance
        before = d_from - dt.timedelta(days=1)
        led = c._collection("Ledger", "Name,ClosingBalance", filt=f"$Name = {_q(ledger)}",
                            from_date=before.replace(month=4, day=1) if before.month >= 4 else
                            before.replace(year=before.year - 1, month=4, day=1), to_date=before)
        el = next(iter(led.iter("LEDGER")), None)
        opening = parse_balance(el.findtext("CLOSINGBALANCE")) if el is not None else Decimal(0)
        root = c._collection(
            "Voucher", "Date,VoucherNumber,VoucherTypeName,Reference,Narration,PartyLedgerName,"
                       "AllLedgerEntries.LedgerName,AllLedgerEntries.Amount",
            filt=f"NOT $IsCancelled AND NOT $IsOptional AND ($PartyLedgerName = {_q(ledger)} "
                 f"OR $$FilterCount:AllLedgerEntries:PUIsDealer > 0)",
            formulae={"PUIsDealer": f"$LedgerName = {_q(ledger)}"},
            from_date=d_from, to_date=d_to, timeout=120)
    except TallyError as e:
        raise AppError(502, "TALLY_ERROR", str(e)) from e

    rows = []
    for v in root.iter("VOUCHER"):
        amt = Decimal(0)
        for le in v.iter("ALLLEDGERENTRIES.LIST"):
            if (le.findtext("LEDGERNAME") or "").strip().lower() == ledger.lower():
                amt += parse_balance(le.findtext("AMOUNT")) or Decimal(0)  # + = debit (dealer owes more)
        if amt == 0:
            continue
        d = (v.findtext("DATE") or "").strip()
        try:
            day = dt.datetime.strptime(d, "%Y%m%d").date()
        except ValueError:
            continue
        rows.append({"date": day, "type": (v.findtext("VOUCHERTYPENAME") or "").strip(),
                     "number": (v.findtext("VOUCHERNUMBER") or "").strip(),
                     "reference": (v.findtext("REFERENCE") or "").strip(),
                     "narration": (v.findtext("NARRATION") or "").strip()[:200],
                     "debit": amt if amt > 0 else Decimal(0), "credit": -amt if amt < 0 else Decimal(0)})
    rows.sort(key=lambda r: (r["date"], r["number"]))
    bal = opening or Decimal(0)
    for r in rows:
        bal += r["debit"] - r["credit"]
        r["balance"] = bal
    fmt = lambda x: f"{x:.2f}"  # noqa: E731
    return {
        "dealer": dealer.name, "ledger": ledger, "from": d_from, "to": d_to,
        "opening": fmt(opening or Decimal(0)), "closing": fmt(bal),
        "total_debit": fmt(sum((r["debit"] for r in rows), Decimal(0))),
        "total_credit": fmt(sum((r["credit"] for r in rows), Decimal(0))),
        "rows": [{**r, "debit": fmt(r["debit"]), "credit": fmt(r["credit"]), "balance": fmt(r["balance"])}
                 for r in rows],
    }
