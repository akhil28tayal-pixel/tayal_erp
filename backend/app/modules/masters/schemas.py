import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ORM = ConfigDict(from_attributes=True)
Category = Literal["CEMENT", "TMT"]


# ------------------------------------------------------------------ company
class CompanyOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    code: str
    legal_name: str
    trade_name: str | None
    gstin: str | None
    is_gst_registered: bool
    state_code: str | None
    doc_prefix: str
    tally_company_name: str | None
    is_demo: bool
    is_active: bool


class CompanyUpdate(BaseModel):
    legal_name: str | None = None
    trade_name: str | None = None
    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    is_gst_registered: bool | None = None
    state_code: str | None = Field(default=None, max_length=2)
    doc_prefix: str | None = Field(default=None, max_length=10)
    tally_company_name: str | None = None


# ------------------------------------------------------------------ employees
class EmployeeIn(BaseModel):
    code: str = Field(max_length=30)
    full_name: str = Field(max_length=200)
    phone: str | None = None
    designation: str | None = None
    reports_to_id: uuid.UUID | None = None
    is_active: bool = True


class EmployeeUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    designation: str | None = None
    reports_to_id: uuid.UUID | None = None
    is_active: bool | None = None


class EmployeeOut(EmployeeIn):
    model_config = ORM
    id: uuid.UUID


# ------------------------------------------------------------------ godowns / owners
class GodownIn(BaseModel):
    code: str = Field(max_length=30)
    name: str = Field(max_length=120)
    kind: Literal["GODOWN", "DIRECT", "SIDING", "VIRTUAL"] = "GODOWN"
    address: str | None = None
    city: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    supervisor_employee_id: uuid.UUID | None = None
    status: Literal["ACTIVE", "INACTIVE", "BLOCKED"] = "ACTIVE"
    supported_categories: list[Category] = ["CEMENT", "TMT"]
    capacity_base_qty: Decimal | None = None
    capacity_uom: str | None = None
    tally_godown_name: str | None = None
    aliases: list[str] = []


class GodownUpdate(BaseModel):
    """Operational fields only. Name and identity come from Tally; use erp-enable/disable to switch on/off."""

    kind: Literal["GODOWN", "DIRECT", "SIDING", "VIRTUAL"] | None = None
    address: str | None = None
    city: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    supervisor_employee_id: uuid.UUID | None = None
    status: Literal["ACTIVE", "INACTIVE", "BLOCKED"] | None = None
    supported_categories: list[Category] | None = None
    capacity_base_qty: Decimal | None = None
    capacity_uom: str | None = None
    aliases: list[str] | None = None


class GodownOut(GodownIn):
    model_config = ORM
    id: uuid.UUID
    source: str
    erp_enabled: bool
    tally_guid: str | None
    tally_parent: str | None
    tally_deleted: bool


class TallyStockItemIn(BaseModel):
    guid: str | None = None
    name: str
    parent: str | None = None
    category: str | None = None
    base_unit: str | None = None


class TallyGodownIn(BaseModel):
    guid: str | None = None
    name: str
    parent: str | None = None


class OwnerIn(BaseModel):
    kind: Literal["OWN_TRADING", "PRINCIPAL_CF", "CONSIGNMENT"]
    name: str
    principal_name: str | None = None
    is_active: bool = True


class OwnerUpdate(BaseModel):
    name: str | None = None
    principal_name: str | None = None
    is_active: bool | None = None


class OwnerOut(OwnerIn):
    model_config = ORM
    id: uuid.UUID


# ------------------------------------------------------------------ brands / products / conversions
class BrandIn(BaseModel):
    code: str = Field(max_length=30)
    name: str
    category: Category
    manufacturer: str | None = None
    is_active: bool = True


class BrandUpdate(BaseModel):
    name: str | None = None
    manufacturer: str | None = None
    is_active: bool | None = None


class BrandOut(BrandIn):
    model_config = ORM
    id: uuid.UUID


class ProductUnitIn(BaseModel):
    model_config = ORM
    uom: str
    is_sellable: bool = True
    is_default_entry: bool = False


class ProductIn(BaseModel):
    code: str = Field(max_length=40)
    name: str
    brand_id: uuid.UUID
    category: Category
    base_uom: str | None = None  # defaults: BAG for cement, KG for TMT
    cement_grade: str | None = None
    bag_weight_kg: Decimal | None = Field(default=None, gt=0)
    tmt_grade: str | None = None
    diameter_mm: int | None = Field(default=None, gt=0)
    length_m: Decimal | None = Field(default=None, gt=0)
    hsn_code: str | None = None
    gst_rate: Decimal | None = None
    is_batch_tracked: bool = False
    shelf_life_days: int | None = None
    is_active: bool = True
    units: list[ProductUnitIn] = []

    @model_validator(mode="after")
    def _category_rules(self):
        if self.category == "CEMENT":
            if self.bag_weight_kg is None:
                raise ValueError("bag_weight_kg is required for cement")
            self.base_uom = self.base_uom or "BAG"
        if self.category == "TMT":
            if self.diameter_mm is None:
                raise ValueError("diameter_mm is required for TMT")
            self.base_uom = self.base_uom or "KG"
        return self


class ProductUpdate(BaseModel):
    """Setup fields. The name comes from Tally. Category/brand/bag weight/diameter complete a product
    imported as 'needs setup'."""

    category: Literal["CEMENT", "TMT", "GENERAL", "OTHER"] | None = None
    base_uom: str | None = None  # for GENERAL (other material): the unit it is stocked and sold in
    brand_id: uuid.UUID | None = None
    bag_weight_kg: Decimal | None = Field(default=None, gt=0)
    diameter_mm: int | None = Field(default=None, gt=0, le=60)
    cement_grade: str | None = None
    tmt_grade: str | None = None
    length_m: Decimal | None = Field(default=None, gt=0)
    hsn_code: str | None = None
    gst_rate: Decimal | None = None
    is_batch_tracked: bool | None = None
    shelf_life_days: int | None = None
    is_active: bool | None = None
    units: list[ProductUnitIn] | None = None


class ProductOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    code: str
    name: str
    brand_id: uuid.UUID | None
    category: str
    base_uom: str
    cement_grade: str | None
    bag_weight_kg: Decimal | None
    tmt_grade: str | None
    diameter_mm: int | None
    length_m: Decimal | None
    hsn_code: str | None
    gst_rate: Decimal | None
    is_batch_tracked: bool
    shelf_life_days: int | None
    is_active: bool
    units: list[ProductUnitIn] = []
    # per-company Tally link and ERP switch
    erp_enabled: bool = True
    source: str = "TALLY"
    tally_name: str | None = None
    tally_parent: str | None = None
    tally_base_unit: str | None = None
    tally_deleted: bool = False
    setup_missing: list[str] = []
    enable_blocked_reason: str | None = None  # why it cannot be switched on (setup, deleted, next phase)


class ConversionIn(BaseModel):
    product_id: uuid.UUID | None = None
    brand_id: uuid.UUID | None = None
    category: Literal["CEMENT", "TMT", "GENERAL"] | None = None
    diameter_mm: int | None = None
    from_uom: str
    to_uom: str
    factor: Decimal = Field(gt=0)
    basis: Literal["NOMINAL", "ACTUAL_AVG", "STANDARD"] = "STANDARD"
    effective_from: date = date(2000, 1, 1)
    effective_to: date | None = None
    source_note: str | None = None

    @model_validator(mode="after")
    def _scope(self):
        if self.product_id and (self.brand_id or self.category):
            raise ValueError("product-level conversion cannot also set brand/category")
        if self.diameter_mm and not (self.brand_id or self.category == "TMT"):
            raise ValueError("diameter_mm needs a brand or category TMT (weight chart)")
        if self.from_uom == self.to_uom:
            raise ValueError("from_uom and to_uom must differ")
        return self


class ConversionOut(ConversionIn):
    model_config = ORM
    id: uuid.UUID


class ConvertIn(BaseModel):
    product_id: uuid.UUID
    qty: Decimal
    from_uom: str
    to_uom: str | None = None
    on: date | None = None


class ConvertOut(BaseModel):
    product_id: uuid.UUID
    qty: Decimal
    from_uom: str
    to_uom: str
    result: Decimal
    factor: Decimal
    path: list[str]


# ------------------------------------------------------------------ dealers (Tally-sourced)
class DealerContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=20, description="Mobile number; +91 / 0 / spaces allowed")
    role: str | None = Field(default=None, max_length=60)
    is_primary: bool = False
    whatsapp_opt_in: bool = False


class DealerContactOut(DealerContactIn):
    model_config = ORM
    id: uuid.UUID


class DealerAddressIn(BaseModel):
    kind: Literal["BILLING", "DELIVERY", "SITE"]
    label: str
    line1: str | None = None
    line2: str | None = None
    area: str | None = None
    city: str | None = None
    state_code: str | None = None
    pincode: str | None = Field(default=None, pattern=r"^\d{6}$")
    lat: Decimal | None = None
    lng: Decimal | None = None
    is_active: bool = True


class DealerAddressOut(DealerAddressIn):
    model_config = ORM
    id: uuid.UUID


class DealerOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    code: str
    name: str
    legal_name: str | None
    tally_ledger_name: str | None
    tally_ledger_guid: str | None
    tally_parent_group: str | None
    source: str
    aliases: list[str]
    gstin: str | None
    category: str
    status: str
    blocked_reason: str | None
    payment_terms_days: int | None
    credit_limit: Decimal | None
    tally_credit_limit: Decimal | None
    tally_credit_period_days: int | None
    tally_balance: Decimal | None = None
    tally_balance_at: datetime | None = None
    tally_mobile: str | None = None
    tally_phone: str | None = None
    pending_in_tally: bool = False
    tally_contact_name: str | None = None
    tally_address: str | None = None
    allowed_overdue_days: int | None
    credit_policy: str
    default_sales_exec_id: uuid.UUID | None
    tally_deleted: bool
    version: int


class DealerDetailOut(DealerOut):
    contacts: list[DealerContactOut] = []
    addresses: list[DealerAddressOut] = []


class DealerCreate(BaseModel):
    """A dealer that isn't in Tally yet, added while booking. It is also made as a ledger in Tally."""

    name: str = Field(min_length=2, max_length=200, description="As it should appear in Tally")
    mobile: str | None = Field(default=None, max_length=20)
    contact_name: str | None = Field(default=None, max_length=120)
    gstin: str | None = Field(default=None, pattern=r"^\s*([0-9]{2}[A-Za-z0-9]{13})?\s*$")
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=60, description="Default Delhi")
    pincode: str | None = Field(default=None, pattern=r"^([0-9]{6})?$")
    group: str | None = Field(default=None, max_length=200, description="Tally group, default Sundry Debtors")
    make_in_tally: bool = True


class DealerCreatedOut(BaseModel):
    dealer: "DealerOut"
    tally: str = Field(description="What happened in Tally, in words")
    in_tally: bool


class DealerUpdate(BaseModel):
    """Only operational fields. Name, GSTIN and ledger identity come from Tally (decision D5)."""

    version: int
    aliases: list[str] | None = None
    category: Literal["DEALER", "RETAILER", "CONTRACTOR", "BUILDER", "INSTITUTION", "OTHER"] | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    credit_limit: Decimal | None = Field(default=None, ge=0)
    allowed_overdue_days: int | None = Field(default=None, ge=0, le=365)
    credit_policy: Literal["STRICT", "WARN", "NONE"] | None = None
    default_sales_exec_id: uuid.UUID | None = None




class BlockIn(BaseModel):
    reason: str = Field(min_length=3)


class TallyLedgerIn(BaseModel):
    """Shape produced by the Tally connector for a Sundry Debtors ledger."""

    guid: str
    name: str
    parent: str | None = None
    gstin: str | None = None
    credit_limit: Decimal | None = None
    credit_period_days: int | None = None
    balance: Decimal | None = None  # closing balance, positive = Dr (dealer owes us)
    mobile: str | None = None  # Tally "Mobile No." — may hold several numbers
    phone: str | None = None
    contact_name: str | None = None
    address: str | None = None


class AssignmentBulkIn(BaseModel):
    dealer_ids: list[uuid.UUID] = Field(min_length=1)
    employee_id: uuid.UUID
    kind: Literal["SALES", "COLLECTION"]
    reason: str | None = None
    effective_from: date | None = None


class AssignmentOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    dealer_id: uuid.UUID
    employee_id: uuid.UUID
    kind: str
    valid_from: date
    valid_to: date | None
    reason: str | None


class DealerRateIn(BaseModel):
    product_id: uuid.UUID
    godown_id: uuid.UUID | None = None
    uom: str
    rate: Decimal = Field(gt=0)
    tolerance_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    valid_from: date
    valid_to: date | None = None


class DealerRateOut(DealerRateIn):
    model_config = ORM
    id: uuid.UUID
    dealer_id: uuid.UUID
    source: str


class ResolvedRate(BaseModel):
    dealer_id: uuid.UUID
    product_id: uuid.UUID
    godown_id: uuid.UUID | None
    uom: str
    rate: Decimal
    min_rate: Decimal
    source: str
    rate_id: uuid.UUID | None


# ------------------------------------------------------------------ transport
class TransporterIn(BaseModel):
    name: str
    gstin: str | None = None
    phone: str | None = None
    is_own_fleet: bool = False
    is_active: bool = True


class TransporterUpdate(BaseModel):
    name: str | None = None
    gstin: str | None = None
    phone: str | None = None
    is_own_fleet: bool | None = None
    is_active: bool | None = None


class TransporterOut(TransporterIn):
    model_config = ORM
    id: uuid.UUID


VehicleType = Literal["TRUCK_6W", "TRUCK_10W", "TRUCK_12W", "TRUCK_14W", "TRAILER", "TRACTOR", "PICKUP", "OTHER"]


class VehicleIn(BaseModel):
    registration_no: str = Field(max_length=20)
    vehicle_type: VehicleType
    capacity_kg: Decimal = Field(gt=0)
    transporter_id: uuid.UUID | None = None
    ownership: Literal["OWN", "ATTACHED", "MARKET"] = "OWN"
    status: Literal["AVAILABLE", "ON_TRIP", "MAINTENANCE", "INACTIVE"] = "AVAILABLE"
    gps_device_id: str | None = None

    @model_validator(mode="after")
    def _normalise(self):
        self.registration_no = self.registration_no.replace(" ", "").replace("-", "").upper()
        return self


class VehicleUpdate(BaseModel):
    vehicle_type: VehicleType | None = None
    capacity_kg: Decimal | None = Field(default=None, gt=0)
    transporter_id: uuid.UUID | None = None
    ownership: Literal["OWN", "ATTACHED", "MARKET"] | None = None
    status: Literal["AVAILABLE", "ON_TRIP", "MAINTENANCE", "INACTIVE"] | None = None
    gps_device_id: str | None = None


class VehicleOut(VehicleIn):
    model_config = ORM
    id: uuid.UUID


class DriverIn(BaseModel):
    name: str
    phone: str
    licence_no: str | None = None
    user_id: uuid.UUID | None = None
    transporter_id: uuid.UUID | None = None
    is_active: bool = True


class DriverUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    licence_no: str | None = None
    user_id: uuid.UUID | None = None
    transporter_id: uuid.UUID | None = None
    is_active: bool | None = None


class DriverOut(DriverIn):
    model_config = ORM
    id: uuid.UUID
