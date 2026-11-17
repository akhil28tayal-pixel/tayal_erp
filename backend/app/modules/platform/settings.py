"""Configurable business rules (no hard-coded rates, limits or godown ids). Values are stored per company in
system_settings as {"v": value}; anything not stored falls back to these defaults."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.platform.models import SystemSetting

DEFAULT_SETTINGS: dict[str, Any] = {
    # orders / credit
    "orders.auto_approve": False,
    "orders.duplicate_window_minutes": 30,
    "orders.auto_close_after_days": 7,
    "credit.limit_source": "ERP_ONLY",  # decision: limits set in the ERP (Tally limit shown for reference)
    "credit.stale_policy": "HOLD",
    "credit.no_limit_policy": "ALLOW",
    "tally.sales_ledger": "",  # Tally sales ledger for delivered orders; empty = the only ledger under Sales Accounts
    "tally.sales_voucher_type": "Sales",
    "tally.url": "",
    # automatic sync (dealers & balances, godowns, products, stock, sales to Tally and their approvals)
    "tally.auto_sync_minutes": 60,  # 0 = off
    "tally.auto_sync_from": "09:00",
    "tally.auto_sync_to": "20:30",  # address of the Tally PC (e.g. http://192.168.0.5:9000); set by the first sync from the web
    "whatsapp.provider": "CLICK_TO_CHAT",
    "whatsapp.sender_name": "",  # name dealers know you by; empty = company trade name, else legal name
    "whatsapp.balance_template": (
        "*Payment Reminder*\nDear {dealer}, your outstanding balance with {sender} is ₹{balance} as on {as_of}.\n\n"
        "Please arrange payment at the earliest. If already paid, kindly ignore this message."
    ),
    # Cloud API: name and language of the template approved in WhatsApp Manager, and what fills its
    # {{1}}, {{2}}, ... in order (any of: dealer, balance, as_of, sender).
    "whatsapp.template_name": "tayal_balance_due",
    "whatsapp.template_language": "en",
    "whatsapp.template_params": "dealer,balance,as_of",
    # WhatsApp to the dealer when a truck is delivered: name of the approved Utility template (empty = off)
    "whatsapp.delivery_template": "",
    "whatsapp.reminder_gap_hours": 24,  # warn before sending the same dealer another reminder within this gap  # ALLOW: dealers without an ERP credit limit are not credit-checked  # D4: stale Tally data → hold for admin
    "tally.stale_threshold_minutes": 120,
    "orders.default_tax_treatment": "NON_GST",  # D1: Ishika Traders Daily books are non-GST
    # products: other building materials (blocks, bricks, sand, chemicals…) are built but switched on next phase
    "products.other_materials_enabled": False,
    # inventory
    "inventory.require_grn_confirmation": True,
    "inventory.cement_age_alert_days": 45,
    "inventory.self_approve_damage_bags": 10,
    # dispatch
    "dispatch.capacity_tolerance_pct": 5,
    "dispatch.pod_min_photos": 1,
    "dispatch.pod_otp_enabled": False,
    # collections
    "collections.aging_buckets": [0, 3, 7, 15, 30],
    "collections.promise_grace_days": 1,
    "collections.promise_fulfil_threshold_pct": 100,
    "collections.priority_weights": {
        "overdue_amount": 35, "overdue_days": 25, "broken_promises": 20, "payment_trend": 10, "exposure": 10,
    },
}


def get_setting(db: Session, company_id: uuid.UUID | None, key: str) -> Any:
    if key not in DEFAULT_SETTINGS:
        raise KeyError(key)
    row = db.execute(
        select(SystemSetting.value).where(SystemSetting.company_id == company_id, SystemSetting.key == key)
    ).scalar_one_or_none()
    return row["v"] if row is not None else DEFAULT_SETTINGS[key]
