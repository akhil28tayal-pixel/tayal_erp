import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import snapshot, write_audit
from app.core.errors import AppError
from app.core.sequences import next_number
from app.modules.masters.models import Dealer, DealerAddress, DealerAssignment, DealerRate, Employee
from app.modules.masters.schemas import TallyLedgerIn

# ------------------------------------------------------------------ dealers from Tally (decision D5)
DOUBTFUL_REASON = "Doubtful Debts group in Tally"


def _is_doubtful(parent: str | None) -> bool:
    return bool(parent) and "doubtful" in parent.lower()


def _apply_doubtful_rule(db: Session, dealer: Dealer) -> bool:
    """Ledgers under Doubtful Debts are blocked for new orders. Moving out of that group lifts only the
    automatic block; a block set by a person stays. An admin's manual unblock is never undone by a sync."""
    from app.modules.platform.models import AuditLog

    if _is_doubtful(dealer.tally_parent_group):
        if dealer.status != "ACTIVE":
            return False
        if dealer.id is not None and db.execute(
            select(AuditLog.id).where(AuditLog.entity_id == str(dealer.id), AuditLog.action == "dealer.unblock")
        ).first():
            return False
        dealer.status, dealer.blocked_reason = "BLOCKED", DOUBTFUL_REASON
        return True
    if dealer.status == "BLOCKED" and dealer.blocked_reason == DOUBTFUL_REASON:
        dealer.status, dealer.blocked_reason = "ACTIVE", None
        return True
    return False


def upsert_dealers_from_tally(
    db: Session, company_id: uuid.UUID, ledgers: list[TallyLedgerIn], actor_id: uuid.UUID | None,
    source: str = "TALLY",
) -> dict:
    """Create/update dealers from Sundry Debtors ledgers, keyed by ledger GUID. Idempotent: re-sending the
    same ledgers changes nothing. Tally owns identity fields (name, GSTIN, credit limit/period); the ERP owns
    operational fields and never overwrites them here."""
    stats = {"created": 0, "updated": 0, "unchanged": 0}
    guids = [lg.guid for lg in ledgers]
    existing = {
        d.tally_ledger_guid: d
        for d in db.execute(
            select(Dealer).where(Dealer.company_id == company_id, Dealer.tally_ledger_guid.in_(guids))
        ).scalars()
    }
    synced_at = datetime.now(UTC)
    for lg in ledgers:
        dealer = existing.get(lg.guid)
        values = {
            "name": lg.name.strip(),
            "tally_ledger_name": lg.name,
            "tally_parent_group": lg.parent,
            "gstin": (lg.gstin or None),
            "tally_credit_limit": lg.credit_limit,
            "tally_credit_period_days": lg.credit_period_days,
            "tally_deleted": False,
        }
        if dealer is None:
            seq = next_number(db, company_id, "DEALER", per_fy=False)
            dealer = Dealer(company_id=company_id, code=f"D{seq:05d}", tally_ledger_guid=lg.guid,
                            source=source, created_by=actor_id, status="ACTIVE", **values)
            _apply_doubtful_rule(db, dealer)
            _set_balance(dealer, lg, synced_at)
            db.add(dealer)
            db.flush()
            write_audit(db, action="dealer.created_from_tally", entity_type="dealers", entity_id=dealer.id,
                        actor_id=actor_id, company_id=company_id, after=snapshot(dealer))
            stats["created"] += 1
            continue
        before = snapshot(dealer)
        changed = False
        for k, v in values.items():
            if getattr(dealer, k) != v:
                setattr(dealer, k, v)
                changed = True
        changed = _apply_doubtful_rule(db, dealer) or changed
        _set_balance(dealer, lg, synced_at)  # balances move daily; not audited as a master change
        if changed:
            dealer.updated_by = actor_id
            db.flush()
            write_audit(db, action="dealer.updated_from_tally", entity_type="dealers", entity_id=dealer.id,
                        actor_id=actor_id, company_id=company_id, before=before, after=snapshot(dealer))
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    return stats


def _set_balance(dealer: Dealer, lg: TallyLedgerIn, synced_at: datetime) -> None:
    if lg.balance is not None:
        dealer.tally_balance, dealer.tally_balance_at = lg.balance, synced_at
    dealer.tally_mobile = (lg.mobile or "")[:100] or None
    dealer.tally_phone = (lg.phone or "")[:100] or None
    dealer.tally_contact_name = (lg.contact_name or "")[:120] or None
    dealer.tally_address = (lg.address or "")[:500] or None


def mark_missing_dealers(db: Session, company_id: uuid.UUID, present_guids: set[str],
                         actor_id: uuid.UUID | None) -> int:
    """After a FULL ledger pull: dealers whose ledger no longer exists in Tally are flagged and made inactive.
    Never deleted — their orders and history stay intact."""
    n = 0
    for d in db.execute(
        select(Dealer).where(Dealer.company_id == company_id, Dealer.source == "TALLY",
                             Dealer.tally_deleted.is_(False))
    ).scalars():
        if d.tally_ledger_guid not in present_guids:
            before = snapshot(d)
            d.tally_deleted, d.status = True, "INACTIVE"
            db.flush()
            write_audit(db, action="dealer.missing_in_tally", entity_type="dealers", entity_id=d.id,
                        actor_id=actor_id, company_id=company_id, before=before, after=snapshot(d))
            n += 1
    return n


def _godown_code(name: str, taken: set[str]) -> str:
    import re

    base = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:26] or "GODOWN"
    code, i = base, 2
    while code in taken:
        code, i = f"{base}_{i}", i + 1
    taken.add(code)
    return code


_VEHICLE_NO = None


def looks_like_vehicle(name: str) -> bool:
    """Tally 'godowns' that are really truck numbers: DL01MA5579, HR38AB2126 (ROCK), UP17AT3451, HR38T3383."""
    import re

    global _VEHICLE_NO
    if _VEHICLE_NO is None:
        _VEHICLE_NO = re.compile(r"^[A-Z]{2}\s?\d{1,2}\s?[A-Z]{0,3}\s?\d{3,4}(\s*\(.*\))?$", re.I)
    return bool(_VEHICLE_NO.match(name.strip()))


def _guess_kind(name: str) -> str:
    n = name.lower()
    if n == "direct" or n.startswith("direct "):
        return "DIRECT"
    if "siding" in n or "rake" in n:
        return "SIDING"
    return "GODOWN"


def upsert_godowns_from_tally(db: Session, company_id: uuid.UUID, godowns: list, actor_id: uuid.UUID | None,
                              full: bool = False, source: str = "TALLY") -> dict:
    """Decision Q9: godown names come from Tally. New godowns arrive switched ON; the ERP-only switch
    (erp_enabled) and operational fields are never touched by a re-sync. Keyed by Tally GUID, else by name."""
    from app.modules.masters.models import Godown

    stats = {"created": 0, "updated": 0, "unchanged": 0, "missing": 0}
    existing = db.execute(select(Godown).where(Godown.company_id == company_id)).scalars().all()
    by_guid = {g.tally_guid: g for g in existing if g.tally_guid}
    by_name = {(g.tally_godown_name or "").lower(): g for g in existing if g.tally_godown_name}
    taken = {g.code for g in existing}
    seen: set = set()
    for t in godowns:
        if t.name.strip().lower() == "main location":  # Tally's built-in default godown
            continue
        g = by_guid.get(t.guid) if t.guid else None
        g = g or by_name.get(t.name.lower())
        if g is None:
            g = Godown(company_id=company_id, code=_godown_code(t.name, taken), name=t.name,
                       tally_godown_name=t.name, tally_guid=t.guid, tally_parent=t.parent,
                       kind=_guess_kind(t.name), source=source, created_by=actor_id,
                       # Truck numbers kept as godowns in Tally arrive switched off; real godowns arrive on.
                       erp_enabled=not looks_like_vehicle(t.name))
            db.add(g)
            db.flush()
            write_audit(db, action="godown.created_from_tally", entity_type="godowns", entity_id=g.id,
                        actor_id=actor_id, company_id=company_id, after=snapshot(g))
            stats["created"] += 1
        else:
            before = snapshot(g)
            vals = {"name": t.name, "tally_godown_name": t.name, "tally_parent": t.parent, "tally_deleted": False}
            if t.guid:
                vals["tally_guid"] = t.guid
            changed = {k: v for k, v in vals.items() if getattr(g, k) != v}
            for k, v in changed.items():
                setattr(g, k, v)
            if changed:
                db.flush()
                write_audit(db, action="godown.updated_from_tally", entity_type="godowns", entity_id=g.id,
                            actor_id=actor_id, company_id=company_id, before=before, after=snapshot(g))
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
        seen.add(g.id)
    if full:
        for g in existing:
            if g.source == "TALLY" and g.id not in seen and not g.tally_deleted:
                before = snapshot(g)
                g.tally_deleted, g.erp_enabled = True, False
                db.flush()
                write_audit(db, action="godown.missing_in_tally", entity_type="godowns", entity_id=g.id,
                            actor_id=actor_id, company_id=company_id, before=before, after=snapshot(g))
                stats["missing"] += 1
    return stats


def effective_credit_limit(dealer: Dealer, limit_source: str = "ERP_ONLY") -> Decimal | None:
    """Decision Q8: credit limits are set in the ERP. Tally's limit is stored for reference only."""
    if limit_source == "ERP_ONLY":
        return dealer.credit_limit
    if limit_source == "ERP_THEN_TALLY":
        return dealer.credit_limit if dealer.credit_limit is not None else dealer.tally_credit_limit
    return dealer.tally_credit_limit if dealer.tally_credit_limit is not None else dealer.credit_limit


# ------------------------------------------------------------------ assignments
def assign_dealers(
    db: Session, company_id: uuid.UUID, dealer_ids: list[uuid.UUID], employee_id: uuid.UUID, kind: str,
    reason: str | None, actor_id: uuid.UUID, effective_from: date | None = None,
) -> list[DealerAssignment]:
    effective_from = effective_from or date.today()
    emp = db.get(Employee, employee_id)
    if emp is None or emp.company_id != company_id or not emp.is_active:
        raise AppError(422, "VALIDATION_ERROR", "Employee not found or inactive in this company")
    dealers = db.execute(
        select(Dealer).where(Dealer.id.in_(dealer_ids), Dealer.company_id == company_id)
    ).scalars().all()
    if len(dealers) != len(set(dealer_ids)):
        raise AppError(422, "VALIDATION_ERROR", "One or more dealers not found in this company")
    created = []
    for d in dealers:
        current = db.execute(
            select(DealerAssignment)
            .where(DealerAssignment.dealer_id == d.id, DealerAssignment.kind == kind,
                   DealerAssignment.valid_to.is_(None))
            .with_for_update()
        ).scalar_one_or_none()
        if current is not None:
            if current.employee_id == employee_id:
                continue
            current.valid_to = effective_from
        db.flush()
        a = DealerAssignment(dealer_id=d.id, employee_id=employee_id, kind=kind, valid_from=effective_from,
                             reason=reason, created_by=actor_id)
        db.add(a)
        db.flush()
        write_audit(db, action=f"dealer.assigned_{kind.lower()}", entity_type="dealers", entity_id=d.id,
                    actor_id=actor_id, company_id=company_id,
                    before={"employee_id": str(current.employee_id) if current else None},
                    after={"employee_id": str(employee_id), "reason": reason})
        created.append(a)
    return created


def assigned_dealer_ids_subquery(employee_id: uuid.UUID | None):
    """Dealers currently assigned (sales or collection) to the employee."""
    return select(DealerAssignment.dealer_id).where(
        DealerAssignment.employee_id == employee_id, DealerAssignment.valid_to.is_(None)
    )


# ------------------------------------------------------------------ customer-specific rates (D5)
def resolve_rate(db: Session, company_id: uuid.UUID, dealer_id: uuid.UUID, product_id: uuid.UUID,
                 godown_id: uuid.UUID | None = None, on: date | None = None) -> dict | None:
    """dealer+godown → dealer → (Step 6) last rate billed in Tally. Returns None if nothing is known,
    in which case the order line requires a rate and goes for rate approval."""
    on = on or date.today()
    rows = db.execute(
        select(DealerRate)
        .where(
            DealerRate.company_id == company_id,
            DealerRate.dealer_id == dealer_id,
            DealerRate.product_id == product_id,
            DealerRate.valid_from <= on,
            or_(DealerRate.valid_to.is_(None), DealerRate.valid_to >= on),
            or_(DealerRate.godown_id.is_(None), DealerRate.godown_id == godown_id),
        )
    ).scalars().all()
    if rows:
        best = max(rows, key=lambda r: (r.godown_id is not None and r.godown_id == godown_id, r.valid_from))
        min_rate = (best.rate * (Decimal(100) - best.tolerance_pct) / Decimal(100)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        return {"dealer_id": dealer_id, "product_id": product_id, "godown_id": best.godown_id,
                "uom": best.uom, "rate": best.rate, "min_rate": min_rate, "source": best.source,
                "rate_id": best.id}
    return last_tally_sale_rate(db, company_id, dealer_id, product_id)


def last_tally_sale_rate(db: Session, company_id: uuid.UUID, dealer_id: uuid.UUID,
                         product_id: uuid.UUID) -> dict | None:
    """Placeholder until the Tally voucher sync lands (Step 6): reads tally_sale_lines."""
    return None


# ------------------------------------------------------------------ search
def _compact(col):
    """lower-case, letters and digits only: 'SONU B.M. (Najafgarh)' → 'sonubmnajafgarh'."""
    return func.regexp_replace(func.lower(col), "[^a-z0-9]", "", "g")


def dealer_search(q: str):
    """Every word typed must appear in the dealer's name, Tally ledger name, code or an alias, in any order and
    ignoring dots, spaces and brackets — "sonu bm" finds "SONU B.M. (NAJAFGARH)"."""
    import re

    words = [w for w in (re.sub(r"[^a-z0-9]", "", x.lower()) for x in q.split()) if w]
    if not words:
        return Dealer.id.isnot(None)
    alias_text = func.coalesce(func.array_to_string(Dealer.aliases, " "), "")
    fields = [_compact(Dealer.name), _compact(func.coalesce(Dealer.tally_ledger_name, "")), _compact(Dealer.code),
              _compact(alias_text)]
    return and_(*[or_(*[f.contains(w, autoescape=True) for f in fields]) for w in words])


def address_text(a: DealerAddress) -> str:
    return ", ".join(x for x in [a.line1, a.line2, a.area, a.city, a.pincode] if x)


def delivery_location(db: Session, dealer: Dealer, address_id) -> tuple[str | None, str | None]:
    """Where an order goes: (text, where it came from). The address picked on the order, else the dealer's
    first saved address in the ERP, else the address on the Tally ledger."""
    if address_id:
        a = db.get(DealerAddress, address_id)
        if a is not None:
            return f"{a.label}: {address_text(a)}" if address_text(a) else a.label, "order"
    prefer = case((DealerAddress.kind == "DELIVERY", 0), (DealerAddress.kind == "SITE", 1), else_=2)
    a = db.execute(select(DealerAddress).where(DealerAddress.dealer_id == dealer.id, DealerAddress.is_active.is_(True))
                   .order_by(prefer, DealerAddress.created_at)).scalars().first()
    if a is not None:
        return f"{a.label}: {address_text(a)}" if address_text(a) else a.label, "dealer"
    if dealer.tally_address:
        return dealer.tally_address, "tally"
    return None, None
