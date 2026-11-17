"""Balance reminders on WhatsApp.

Provider today is CLICK_TO_CHAT: the server builds the message and a wa.me link; the person's own WhatsApp
opens with the text filled in and they press send. Nothing is sent automatically, so no Meta account is needed.
When the WhatsApp Business Platform is connected, `whatsapp.provider = CLOUD_API` sends a pre-approved template
from the server instead (not built yet — needs the business number and template approval).
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import AppError
from app.modules.collections import whatsapp_cloud
from app.modules.collections.models import DealerMessage
from app.modules.masters.models import Company, Dealer, DealerContact
from app.modules.platform.settings import get_setting

MOBILE = re.compile(r"^[6-9]\d{9}$")
_IST = ZoneInfo("Asia/Kolkata")  # dates in messages are Indian dates whatever the server time zone


def normalize_mobile(raw: str | None) -> str | None:
    """First Indian mobile number in a free-text field → '91XXXXXXXXXX'. Tally fields often hold several numbers
    ('9810012345 / 011-2345678'), a leading 0 or +91, spaces or dashes. Landlines are skipped."""
    if not raw:
        return None
    for part in re.split(r"[,/;|\n]+|\s{2,}", raw):
        digits = re.sub(r"\D", "", part)
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
        if MOBILE.match(digits):
            return "91" + digits
    # numbers written with single spaces inside them ('98100 12345')
    digits = re.sub(r"\D", "", raw)
    for cand in (digits[-10:], digits[:10]):
        if MOBILE.match(cand) and len(digits) in (10, 11, 12):
            return "91" + cand
    return None


def whatsapp_numbers(db: Session, dealer: Dealer) -> list[dict]:
    """Every usable mobile for the dealer, best first: primary ERP contact, other ERP contacts, Tally mobile,
    Tally phone."""
    out: list[dict] = []
    contacts = db.execute(
        select(DealerContact).where(DealerContact.dealer_id == dealer.id)
        .order_by(DealerContact.is_primary.desc(), DealerContact.created_at)
    ).scalars().all()
    for c in contacts:
        out.append({"number": normalize_mobile(c.phone), "label": c.name + (" (primary)" if c.is_primary else "")})
    out.append({"number": normalize_mobile(dealer.tally_mobile),
                "label": f"Tally mobile{f' · {dealer.tally_contact_name}' if dealer.tally_contact_name else ''}"})
    out.append({"number": normalize_mobile(dealer.tally_phone), "label": "Tally phone"})
    seen, result = set(), []
    for o in out:
        if o["number"] and o["number"] not in seen:
            seen.add(o["number"])
            result.append(o)
    return result


def inr(amount: Decimal) -> str:
    """Indian grouping: 1234567.5 → '12,34,567.50'; whole rupees drop the paise."""
    q = amount.quantize(Decimal("0.01"))
    whole, frac = f"{abs(q):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    s = ",".join(groups + [tail]) if groups else tail
    return ("-" if q < 0 else "") + s + ("" if frac == "00" else "." + frac)


def sender_name(db: Session, company: Company) -> str:
    return get_setting(db, company.id, "whatsapp.sender_name") or company.trade_name or company.legal_name


def balance_message(db: Session, company: Company, dealer: Dealer) -> str:
    as_of = dealer.tally_balance_at.astimezone(_IST).strftime("%d %b %Y") if dealer.tally_balance_at else "today"
    text = get_setting(db, company.id, "whatsapp.balance_template")
    return (text.replace("{dealer}", dealer.name.strip()).replace("{sender}", sender_name(db, company))
            .replace("{balance}", inr(dealer.tally_balance or Decimal(0))).replace("{as_of}", as_of))


def last_reminders(db: Session, dealer_ids: list[uuid.UUID]) -> dict[uuid.UUID, DealerMessage]:
    if not dealer_ids:
        return {}
    rows = db.execute(
        select(DealerMessage).where(DealerMessage.dealer_id.in_(dealer_ids), DealerMessage.kind == "BALANCE",
                                    DealerMessage.status != "FAILED")  # a failed attempt is not a reminder
        .order_by(DealerMessage.dealer_id, DealerMessage.created_at.desc())
        .distinct(DealerMessage.dealer_id)
    ).scalars()
    return {m.dealer_id: m for m in rows}


def prepare_balance_message(db: Session, company_id: uuid.UUID, dealer: Dealer, actor_id: uuid.UUID,
                            number: str | None, confirm_repeat: bool) -> tuple[DealerMessage, str | None]:
    if dealer.tally_balance is None:
        raise AppError(422, "NO_BALANCE", "No Tally balance yet for this dealer. Run the Tally sync first.")
    if dealer.tally_balance <= 0:
        raise AppError(422, "NOTHING_DUE", f"{dealer.name} has nothing outstanding in Tally")
    numbers = [n["number"] for n in whatsapp_numbers(db, dealer)]
    if number:
        number = normalize_mobile(number)
        if number is None:
            raise AppError(422, "INVALID_MOBILE", "That is not a valid Indian mobile number")
    elif numbers:
        number = numbers[0]
    else:
        raise AppError(422, "NO_MOBILE", f"No mobile number for {dealer.name}. Add one under the dealer's "
                       "contacts, or in the ledger's Mobile No. in Tally and sync again.")
    gap = timedelta(hours=int(get_setting(db, company_id, "whatsapp.reminder_gap_hours")))
    last = last_reminders(db, [dealer.id]).get(dealer.id)
    if last and datetime.now(UTC) - last.created_at < gap and not confirm_repeat:
        raise AppError(409, "RECENTLY_REMINDED",
                       f"A balance reminder was sent to {dealer.name} on "
                       f"{last.created_at.astimezone(_IST).strftime('%d %b, %I:%M %p')}. Send again?")
    company = db.get(Company, company_id)
    provider = get_setting(db, company_id, "whatsapp.provider")
    body = balance_message(db, company, dealer)
    msg = DealerMessage(company_id=company_id, dealer_id=dealer.id, channel="WHATSAPP", kind="BALANCE",
                        provider=provider, phone=number, body=body, balance=dealer.tally_balance,
                        status="OPENED" if provider == "CLICK_TO_CHAT" else "SENT", created_by=actor_id)
    db.add(msg)
    db.flush()
    write_audit(db, action="dealer.whatsapp_balance", entity_type="dealers", entity_id=dealer.id, actor_id=actor_id,
                company_id=company_id, after={"phone": number, "balance": str(dealer.tally_balance),
                                              "provider": provider})
    if provider == "CLICK_TO_CHAT":
        return msg, f"https://wa.me/{number}?text={quote(body)}"
    # CLOUD_API: sent straight from the server with the approved template.
    try:
        msg.provider_message_id = whatsapp_cloud.send_template(
            number, get_setting(db, company_id, "whatsapp.template_name"),
            get_setting(db, company_id, "whatsapp.template_language"), balance_params(db, company, dealer))
    except whatsapp_cloud.WhatsAppError as e:
        msg.status, msg.error = "FAILED", str(e)
        db.commit()  # keep the failed attempt in the log
        raise AppError(502, "WHATSAPP_SEND_FAILED", str(e)) from e
    return msg, None


PARAM_FIELDS = ("dealer", "balance", "as_of", "sender")


def balance_values(db: Session, company: Company, dealer: Dealer) -> dict[str, str]:
    as_of = dealer.tally_balance_at.astimezone(_IST).strftime("%d %b %Y") if dealer.tally_balance_at else "today"
    return {"dealer": dealer.name.strip(), "balance": inr(dealer.tally_balance or Decimal(0)), "as_of": as_of,
            "sender": sender_name(db, company)}


def template_params(db: Session, company_id: uuid.UUID, values: dict[str, str]) -> list[str]:
    """Values for the approved template's {{1}}, {{2}}, ... in the order set in whatsapp.template_params."""
    order = [f.strip() for f in str(get_setting(db, company_id, "whatsapp.template_params")).split(",") if f.strip()]
    bad = [f for f in order if f not in PARAM_FIELDS]
    if bad or not order:
        raise AppError(422, "VALIDATION_ERROR", f"whatsapp.template_params must list {', '.join(PARAM_FIELDS)}")
    return [values[f] for f in order]


def balance_params(db: Session, company: Company, dealer: Dealer) -> list[str]:
    return template_params(db, company.id, balance_values(db, company, dealer))
