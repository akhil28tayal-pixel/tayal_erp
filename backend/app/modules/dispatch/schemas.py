import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

Qty = Decimal


class DOLineIn(BaseModel):
    order_item_id: uuid.UUID
    qty: Decimal = Field(gt=0, max_digits=15, decimal_places=3, description="In the order line's unit")


class DOCreate(BaseModel):
    sales_order_id: uuid.UUID
    godown_id: uuid.UUID | None = Field(default=None, description="Required with vehicle_id; a direct truck brings its own")
    vehicle_id: uuid.UUID | None = Field(default=None, description="Own truck. Give this or direct_truck_id.")
    direct_truck_id: uuid.UUID | None = Field(
        default=None, description="Factory truck in the Direct godown: delivered straight from it, so the delivery "
                                  "order is sent out at once (no loading step)")
    direct_truck_no: str | None = Field(
        default=None, max_length=40,
        description="From the Direct godown on a factory truck not yet in the Direct trucks list: its number")
    send_now: bool = Field(default=False, description="Own truck: plan and send out in one step (needs the driver)")
    driver_id: uuid.UUID | None = None
    dispatch_date: date | None = None
    supplier_ref: str | None = Field(default=None, max_length=100)
    remarks: str | None = Field(default=None, max_length=1000)
    lines: list[DOLineIn] = Field(min_length=1)
    capacity_override_reason: str | None = Field(default=None, max_length=500)
    allow_more: bool = Field(default=False, description="Send more than is left on the order: the order line is "
                                                         "increased when the truck goes out")
    close_rest: bool = Field(default=False, description="With send_now / a direct truck: what is left on these "
                                                        "order lines after this truck will not be sent; close it")


class DOUpdate(BaseModel):
    version: int
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    dispatch_date: date | None = None
    remarks: str | None = Field(default=None, max_length=1000)
    capacity_override_reason: str | None = Field(default=None, max_length=500)


class LoadLineIn(BaseModel):
    do_item_id: uuid.UUID
    qty: Decimal = Field(ge=0, max_digits=15, decimal_places=3)
    actual_weight_kg: Decimal | None = Field(default=None, ge=0)


class LoadIn(BaseModel):
    lines: list[LoadLineIn] = Field(default_factory=list, description="Empty = loaded exactly as planned")
    remarks: str | None = Field(default=None, max_length=1000)


class DispatchIn(BaseModel):
    driver_id: uuid.UUID | None = None
    lines: list[LoadLineIn] = Field(default_factory=list,
                                    description="Quantities leaving the gate; empty = as loaded (or as planned)")
    remarks: str | None = Field(default=None, max_length=1000)
    allow_more: bool = Field(default=False, description="A line may go out with more than planned or than is left "
                                                         "on the order; the order line is increased to match")
    close_rest: bool = Field(default=False, description="What is left on these order lines after this truck will "
                                                        "not be sent: close it")


class PodLineIn(BaseModel):
    do_item_id: uuid.UUID
    delivered_qty: Decimal = Field(ge=0, max_digits=15, decimal_places=3)
    short_qty: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=3)
    damaged_qty: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=3)
    returned_qty: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=3,
                                  description="Came back with the truck (the dealer took less): goes back to the order")


class DeliverIn(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=120)
    recipient_phone: str | None = Field(default=None, max_length=20)
    lines: list[PodLineIn] = Field(default_factory=list, description="Empty = everything delivered")
    remarks: str | None = Field(default=None, max_length=1000)
    delivered_on: date | None = Field(default=None, description="Day it was delivered, for a delivery recorded "
                                                                 "later (default today; not in the future)")
    allow_more: bool = Field(default=False, description="The truck carried more than was recorded as sent: the extra "
                                                         "counts as sent, and the order line is increased if needed")
    close_rest: bool = Field(default=False, description="What is left on these order lines (including anything that "
                                                        "came back) will not be sent: close it")


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class DOItemOut(BaseModel):
    id: uuid.UUID
    line_no: int
    order_item_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    uom: str
    planned_qty: Decimal
    loaded_qty: Decimal | None
    dispatched_qty: Decimal
    delivered_qty: Decimal
    short_qty: Decimal
    damaged_qty: Decimal
    actual_weight_kg: Decimal | None
    order_open_qty: Decimal | None = Field(
        default=None, description="Still to be sent on the order line, not counting what has left the gate and "
                                  "other trucks at the gate (includes this one while it is at the gate)")


class DOEventOut(BaseModel):
    event: str
    notes: str | None
    actor_name: str | None
    at: datetime


class DOOut(BaseModel):
    id: uuid.UUID
    do_no: str
    status: str
    sales_order_id: uuid.UUID
    order_no: str | None
    dealer_id: uuid.UUID
    dealer_name: str
    site_name: str | None
    delivery_address: str | None
    godown_id: uuid.UUID
    godown_name: str
    vehicle_id: uuid.UUID | None
    vehicle_no: str
    vehicle_capacity_kg: Decimal | None
    direct_truck_id: uuid.UUID | None = None
    source: str = "OWN"  # OWN | DIRECT
    driver_id: uuid.UUID | None
    driver_name: str | None
    driver_phone: str | None
    dispatch_date: date
    supplier_ref: str | None
    remarks: str | None
    planned_weight_kg: Decimal | None
    capacity_override_reason: str | None
    loaded_at: datetime | None
    dispatched_at: datetime | None
    delivered_at: datetime | None
    recipient_name: str | None
    recipient_phone: str | None
    delivery_remarks: str | None
    cancel_reason: str | None
    version: int
    summary: str
    items: list[DOItemOut]
    events: list[DOEventOut] = []
    can: list[str] = []


class PendingLineOut(BaseModel):
    order_id: uuid.UUID
    order_no: str | None
    order_date: date
    priority: str
    requested_delivery_date: date | None
    dealer_id: uuid.UUID
    dealer_name: str
    site_name: str | None
    order_item_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    godown_id: uuid.UUID | None
    godown_name: str | None
    uom: str
    ordered_qty: Decimal
    dispatched_qty: Decimal
    planned_qty: Decimal
    remaining_qty: Decimal
    weight_per_unit_kg: Decimal | None


class VehicleLoadOut(BaseModel):
    vehicle_id: uuid.UUID
    registration_no: str
    capacity_kg: Decimal
    status: str
    planned_kg: Decimal
    planned_dos: int
    on_road_dos: int


class OptionLineOut(BaseModel):
    order_item_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    uom: str
    remaining_qty: Decimal


class GodownOptionLine(BaseModel):
    order_item_id: uuid.UUID
    stock_qty: Decimal | None  # in the order line's unit; None = Tally item not linked to this product
    reserved_qty: Decimal
    available_qty: Decimal | None


class GodownOption(BaseModel):
    godown_id: uuid.UUID
    name: str
    kind: str
    lines: list[GodownOptionLine]
    enough: bool  # every remaining line can be covered from here
    has_stock: bool


class TruckOptionLine(BaseModel):
    tally_item: str
    product_id: uuid.UUID | None
    order_item_id: uuid.UUID | None  # the order line this truck can serve
    qty: Decimal  # as invoiced (bags)
    uom: str
    left_qty: Decimal | None  # in the order line's unit when it matches the order, else in `uom`


class TruckOption(BaseModel):
    id: uuid.UUID
    truck_no: str
    supplier: str
    invoice_no: str
    invoice_date: date
    source: str | None
    lines: list[TruckOptionLine]
    matches: bool  # carries something this order still needs


class DispatchOptionsOut(BaseModel):
    lines: list[OptionLineOut]
    stock_as_of: datetime | None
    stock_refreshing: bool
    stock_error: str | None
    godowns: list[GodownOption]
    direct_trucks: list[TruckOption]
