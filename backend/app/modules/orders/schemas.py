import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ORM = ConfigDict(from_attributes=True)


class OrderLineIn(BaseModel):
    product_id: uuid.UUID
    qty: Decimal = Field(gt=0, max_digits=15, decimal_places=3)
    uom: str = Field(min_length=1, max_length=10)
    godown_id: uuid.UUID | None = Field(default=None, description="Godown to load from")
    rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4,
                                 description="Leave empty to use the dealer's rate")
    rate_uom: str | None = Field(default=None, max_length=10, description="Unit the rate is per (default: the dealer rate's unit)")
    remarks: str | None = Field(default=None, max_length=300)


class OrderIn(BaseModel):
    dealer_id: uuid.UUID
    order_date: date | None = None
    delivery_address_id: uuid.UUID | None = None
    site_name: str | None = Field(default=None, max_length=200)
    requested_delivery_date: date | None = None
    priority: Literal["NORMAL", "URGENT"] = "NORMAL"
    source: Literal["OFFICE", "SALES_EXEC", "PHONE", "WHATSAPP"] = "OFFICE"
    special_instructions: str | None = Field(default=None, max_length=2000)
    lines: list[OrderLineIn] = Field(min_length=1, max_length=50)


class OrderCreate(OrderIn):
    submit: bool = Field(default=True, description="Submit straight away (false = save as draft)")
    confirm_duplicate: bool = False
    # Set by the phone for an order booked while the network was weak and sent later. Sending the same booking
    # again returns the order already made instead of a second one.
    client_ref: str | None = Field(default=None, min_length=8, max_length=64)


class OrderUpdate(OrderIn):
    version: int
    submit: bool = False
    confirm_duplicate: bool = False


class SubmitIn(BaseModel):
    confirm_duplicate: bool = False


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class ApproveLineIn(BaseModel):
    line_no: int = Field(ge=1)
    qty: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=3,
                                description="New quantity, in the unit the line was booked in")
    rate: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=4,
                                 description="New rate, per the line's rate unit")


class ApproveIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
    override_credit: bool = Field(default=False, description="Admin: approve although the credit check fails")
    lines: list[ApproveLineIn] = Field(default_factory=list, description="Super admin: change quantity / rate "
                                                                         "of these lines, then approve")


class PricedLineOut(BaseModel):
    line_no: int
    product_id: uuid.UUID
    product_name: str
    godown_id: uuid.UUID | None
    qty: Decimal
    uom: str
    base_qty: Decimal
    base_uom: str
    rate: Decimal
    rate_uom: str
    rate_qty: Decimal
    list_rate: Decimal | None
    min_rate: Decimal | None
    rate_source: str
    amount: Decimal
    weight_kg: Decimal | None
    flags: list[str]


class CreditPosition(BaseModel):
    result: Literal["PASS", "WARN", "HOLD"]
    credit_limit: Decimal | None
    tally_balance: Decimal | None
    balance_as_of: datetime | None
    is_stale: bool
    open_order_exposure: Decimal
    this_order_amount: Decimal
    available_credit: Decimal | None
    reasons: list[dict]


class PreviewOut(BaseModel):
    lines: list[PricedLineOut]
    total_amount: Decimal
    total_weight_kg: Decimal | None
    credit: CreditPosition


class OrderItemOut(BaseModel):
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    product_name: str
    product_code: str
    godown_id: uuid.UUID | None
    godown_name: str | None
    qty: Decimal
    uom: str
    base_qty: Decimal
    base_uom: str
    rate: Decimal
    rate_uom: str
    list_rate: Decimal | None
    min_rate: Decimal | None
    rate_source: str
    amount: Decimal
    cancelled_qty: Decimal
    dispatched_qty: Decimal
    delivered_qty: Decimal
    planned_qty: Decimal  # on delivery orders not yet dispatched
    remaining_qty: Decimal  # still to be planned for dispatch
    weight_per_unit_kg: Decimal | None  # kg in one `uom`; None when the product has no weight conversion
    status: str
    remarks: str | None


class OrderSummaryOut(BaseModel):
    id: uuid.UUID
    order_no: str | None
    order_date: date
    dealer_id: uuid.UUID
    dealer_name: str
    dealer_code: str
    status: str
    hold_kind: str | None
    priority: str
    total_amount: Decimal
    summary: str
    booked_by_name: str | None
    sales_exec_name: str | None
    created_at: datetime


class HistoryOut(BaseModel):
    model_config = ORM
    from_status: str | None
    to_status: str
    action: str
    reason: str | None
    actor_name: str | None = None
    at: datetime


class CreditCheckOut(BaseModel):
    model_config = ORM
    checked_at: datetime
    stage: str
    tally_balance: Decimal | None
    balance_as_of: datetime | None
    is_stale: bool
    credit_limit: Decimal | None
    open_order_exposure: Decimal
    this_order_amount: Decimal
    available_credit: Decimal | None
    result: str
    reasons: list[dict]


class OrderDOOut(BaseModel):
    id: uuid.UUID
    do_no: str
    status: str
    vehicle_no: str
    dispatch_date: date


class CreditNowOut(BaseModel):
    """The dealer's position right now (not when the order was booked), shown big when approving."""
    tally_balance: Decimal | None  # Dr = dealer owes
    balance_as_of: datetime | None
    is_stale: bool
    credit_limit: Decimal | None
    open_order_exposure: Decimal  # other approved orders not yet sent
    this_order_amount: Decimal
    total_after: Decimal  # balance + other open orders + this order
    available_credit: Decimal | None  # before this order
    result: str


class OrderOut(OrderSummaryOut):
    credit_now: CreditNowOut | None = None
    tally_status: str | None = Field(default=None, description="Delivered order in Tally: PENDING | FAILED | "
                                     "WAITING_APPROVAL | APPROVED | REJECTED")
    tally_note: str | None = None
    delivery_address_id: uuid.UUID | None
    delivery_location: str | None = Field(default=None, description="Where the material goes, as text")
    delivery_location_source: str | None = Field(default=None, description="order | dealer | tally")
    dealer_phone: str | None = None
    site_name: str | None
    requested_delivery_date: date | None
    source: str
    special_instructions: str | None
    hold_reason: str | None
    review_reasons: list[dict]
    cancel_reason: str | None
    submitted_at: datetime | None
    approved_at: datetime | None
    approved_by_name: str | None
    created_by: uuid.UUID | None
    version: int
    items: list[OrderItemOut]
    history: list[HistoryOut]
    credit_checks: list[CreditCheckOut]
    delivery_orders: list[OrderDOOut]
    can: list[str] = Field(description="Actions the current user can take on this order now")


class LastOrderLineOut(BaseModel):
    product_id: uuid.UUID
    product_name: str
    qty: Decimal
    uom: str
    godown_id: uuid.UUID | None
    godown_name: str | None
    rate: Decimal


class LastOrderOut(BaseModel):
    order_id: uuid.UUID
    order_no: str | None
    order_date: date
    status: str
    site_name: str | None
    lines: list[LastOrderLineOut]


class HistoryEntryOut(BaseModel):
    at: datetime
    who: str | None
    what: str
    detail: str | None = None
    kind: str  # order | truck | tally | whatsapp
