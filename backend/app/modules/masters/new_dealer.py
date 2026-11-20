"""A new dealer added while booking an order, before it exists in Tally.

The dealer is made in the ERP straight away (so the order can be booked) and as a party ledger in Tally. If Tally
can't be reached it is made there at the next Tally sync. The next sync links the two by name, after which the dealer
is like any other one that came from Tally.
"""

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import snapshot, write_audit
from app.core.deps import Ctx
from app.core.errors import AppError
from app.core.sequences import next_number
from app.modules.masters.models import Dealer, DealerAssignment, DealerContact

DEFAULT_GROUP = "Sundry Debtors"


def groups(db: Session, company_id: uuid.UUID) -> list[str]:
    """Tally groups the dealers are in, most used first ("Sundry Debtors" always offered)."""
    rows = db.execute(select(Dealer.tally_parent_group, func.count())
                      .where(Dealer.company_id == company_id, Dealer.tally_parent_group.is_not(None))
                      .group_by(Dealer.tally_parent_group).order_by(func.count().desc())).all()
    out = [g for g, _ in rows if g]
    return out if DEFAULT_GROUP in out else out + [DEFAULT_GROUP]


def pending(dealer: Dealer) -> bool:
    """Made in the ERP and not yet seen in Tally."""
    return dealer.source == "ERP" and dealer.tally_ledger_guid is None


def _address(d: Dealer) -> list[str]:
    return [x for x in (d.tally_address or "").split("\n") if x.strip()]


def make_in_tally(db: Session, dealer: Dealer) -> str | None:
    """Make the dealer's ledger in Tally. Returns None when done, or why it couldn't be."""
    from app.modules.integrations.tally.sales import _client
    from app.modules.purchases.tally_io import TallyError

    try:
        r = _client(db, dealer.company_id).create_ledger(
            dealer.tally_ledger_name or dealer.name, parent=dealer.tally_parent_group or DEFAULT_GROUP,
            mobile=dealer.tally_mobile, address=_address(dealer), state=dealer.tally_state, pincode=dealer.tally_pincode,
            gstin=dealer.gstin, contact=dealer.tally_contact_name)
    except (TallyError, ValueError) as e:
        return str(e)
    return None if r["ok"] else r["error"]


def create(db: Session, ctx: Ctx, body) -> tuple[Dealer, str]:
    name = " ".join(body.name.split())
    gstin = (body.gstin or "").strip().upper() or None
    hit = db.execute(select(Dealer).where(
        Dealer.company_id == ctx.company_id,
        (func.lower(Dealer.name) == name.lower()) | (func.lower(Dealer.tally_ledger_name) == name.lower())
        | ((Dealer.gstin == gstin) if gstin else False))).scalars().first()
    if hit is not None:
        raise AppError(409, "DEALER_EXISTS", f"{hit.name} is already a dealer"
                       + (" with this GSTIN" if gstin and hit.gstin == gstin else "") + ". Pick it from the list.",
                       dealer_id=str(hit.id), dealer_name=hit.name)
    from app.modules.collections.service import normalize_mobile

    mobile = normalize_mobile(body.mobile) if body.mobile else None
    if body.mobile and mobile is None:
        raise AppError(422, "VALIDATION_ERROR", "Enter a 10-digit mobile number", field="mobile")
    seq = next_number(db, ctx.company_id, "DEALER", per_fy=False)
    address = "\n".join(x for x in (body.address, body.city) if x and x.strip()) or None
    dealer = Dealer(company_id=ctx.company_id, code=f"D{seq:05d}", name=name, tally_ledger_name=name, source="ERP",
                    tally_parent_group=body.group or DEFAULT_GROUP, gstin=gstin, status="ACTIVE",
                    tally_mobile=mobile[2:] if mobile else None, tally_contact_name=body.contact_name or None,
                    tally_address=address, created_by=ctx.user_id)
    dealer.tally_state, dealer.tally_pincode = body.state or "Delhi", body.pincode or None
    db.add(dealer)
    db.flush()
    if mobile:
        db.add(DealerContact(dealer_id=dealer.id, name=body.contact_name or name, phone=mobile[2:], is_primary=True,
                             whatsapp_opt_in=True, created_by=ctx.user_id))
    # Staff who only see their own dealers must be able to book for the one they just added.
    if not ctx.has("dealers.view_all") and ctx.user.employee_id:
        db.add(DealerAssignment(dealer_id=dealer.id, employee_id=ctx.user.employee_id, kind="SALES",
                                valid_from=date.today(), reason="Added by them while booking"))
    write_audit(db, action="dealer.created_in_erp", entity_type="dealers", entity_id=dealer.id, actor_id=ctx.user_id,
                company_id=ctx.company_id, after=snapshot(dealer))
    db.flush()
    if not body.make_in_tally:
        return dealer, "Not made in Tally yet: it will be made at the next Tally sync."
    err = make_in_tally(db, dealer)
    if err is None:
        return dealer, f"Made in Tally under {dealer.tally_parent_group}."
    return dealer, f"Not made in Tally yet ({err}). It will be tried again at the next Tally sync."


def push_pending(db: Session, company_id: uuid.UUID) -> dict:
    """At a Tally sync: make the ledgers of dealers added in the ERP that Tally doesn't have yet."""
    made, failed = [], []
    for d in db.execute(select(Dealer).where(Dealer.company_id == company_id, Dealer.source == "ERP",
                                             Dealer.tally_ledger_guid.is_(None))).scalars():
        err = make_in_tally(db, d)
        (made if err is None else failed).append(d.name if err is None else f"{d.name}: {err}")
    return {"made": made, "failed": failed}
