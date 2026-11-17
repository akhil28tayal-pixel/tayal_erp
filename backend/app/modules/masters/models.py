import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, CompanyMixin, IdMixin, TimestampMixin

Money = Numeric(18, 2)
Rate = Numeric(18, 4)
Qty = Numeric(18, 3)
BaseQty = Numeric(20, 6)
Factor = Numeric(20, 8)


def enum_check(column: str, values: list[str], name: str) -> CheckConstraint:
    quoted = ",".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


# ---------------------------------------------------------------- company & people
class Company(IdMixin, TimestampMixin, Base):
    __tablename__ = "companies"

    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    is_gst_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    address: Mapped[dict | None] = mapped_column(JSONB)
    state_code: Mapped[str | None] = mapped_column(String(2))
    doc_prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    tally_company_name: Mapped[str | None] = mapped_column(String(200))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Employee(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    designation: Mapped[str | None] = mapped_column(String(100))
    reports_to_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("employees.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------- godowns & owners
GODOWN_KINDS = ["GODOWN", "DIRECT", "SIDING", "VIRTUAL"]
GODOWN_STATUS = ["ACTIVE", "INACTIVE", "BLOCKED"]


class Godown(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "godowns"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        UniqueConstraint("company_id", "tally_guid"),
        enum_check("kind", GODOWN_KINDS, "kind"),
        enum_check("status", GODOWN_STATUS, "status"),
        enum_check("source", ["TALLY", "SYSTEM", "DEMO"], "source"),
    )

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="GODOWN", nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(80))
    contact_person: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    supervisor_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id")
    )
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    supported_categories: Mapped[list[str]] = mapped_column(
        ARRAY(String(10)), default=lambda: ["CEMENT", "TMT"], nullable=False
    )
    capacity_base_qty: Mapped[Decimal | None] = mapped_column(BaseQty)
    capacity_uom: Mapped[str | None] = mapped_column(String(10))
    tally_godown_name: Mapped[str | None] = mapped_column(String(200))
    tally_guid: Mapped[str | None] = mapped_column(String(100))
    tally_parent: Mapped[str | None] = mapped_column(String(200))
    tally_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Godowns come from Tally; ones not used operationally are switched off here (they stay in Tally).
    source: Mapped[str] = mapped_column(String(10), default="TALLY", nullable=False)
    erp_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list, nullable=False)


OWNER_KINDS = ["OWN_TRADING", "PRINCIPAL_CF", "CONSIGNMENT"]


class InventoryOwner(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "inventory_owners"
    __table_args__ = (enum_check("kind", OWNER_KINDS, "kind"), UniqueConstraint("company_id", "name"))

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    principal_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class OwnerSaleRule(Base):
    """The only way stock of one owner may be sold by a company. OWN_TRADING owner of the same company
    is implicitly allowed; everything else needs an explicit row."""

    __tablename__ = "owner_sale_rules"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inventory_owners.id"), primary_key=True
    )
    selling_company_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id"), primary_key=True
    )
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------- products & units
CATEGORIES = ["CEMENT", "TMT"]


class Uom(Base):
    __tablename__ = "uoms"
    __table_args__ = (enum_check("kind", ["COUNT", "WEIGHT", "LENGTH", "VOLUME", "LOAD"], "kind"),)

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)


class Brand(IdMixin, TimestampMixin, Base):
    __tablename__ = "brands"
    __table_args__ = (enum_check("category", CATEGORIES, "category"),)

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# GENERAL = other building material (blocks, bricks, sand, chemicals…), stocked in its own Tally unit.
# OTHER = imported from Tally, not yet classified.
PRODUCT_CATEGORIES = ["CEMENT", "TMT", "GENERAL", "OTHER"]


class Product(IdMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        enum_check("category", PRODUCT_CATEGORIES, "category"),
        CheckConstraint(
            "(category <> 'CEMENT') OR (bag_weight_kg IS NOT NULL AND bag_weight_kg > 0)", name="cement_bag_wt"
        ),
        CheckConstraint("(category <> 'TMT') OR (diameter_mm IS NOT NULL)", name="tmt_diameter"),
    )

    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    base_uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), nullable=False)
    cement_grade: Mapped[str | None] = mapped_column(String(30))
    bag_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    tmt_grade: Mapped[str | None] = mapped_column(String(20))
    diameter_mm: Mapped[int | None] = mapped_column(Integer)
    length_m: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    is_batch_tracked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    brand: Mapped[Brand | None] = relationship(lazy="joined")
    units: Mapped[list["ProductUnit"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    def missing_setup(self) -> list[str]:
        """What still has to be filled in before the product can be ordered or stocked."""
        missing = []
        if self.category not in ("CEMENT", "TMT", "GENERAL"):
            missing.append("category")
        if self.brand_id is None and self.category != "GENERAL":  # brand optional for other materials
            missing.append("brand")
        if self.category == "CEMENT" and not self.bag_weight_kg:
            missing.append("bag weight")
        if self.category == "TMT" and not self.diameter_mm:
            missing.append("diameter")
        return missing


class ProductUnit(Base):
    __tablename__ = "product_units"

    product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), primary_key=True)
    is_sellable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default_entry: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProductConversion(IdMixin, TimestampMixin, Base):
    """1 from_uom = factor × to_uom. Scope: product > brand(+diameter) > category > global (all NULL)."""

    __tablename__ = "product_conversions"
    __table_args__ = (
        CheckConstraint("factor > 0", name="factor_positive"),
        CheckConstraint("from_uom <> to_uom", name="distinct_uoms"),
        enum_check("basis", ["NOMINAL", "ACTUAL_AVG", "STANDARD"], "basis"),
        Index("ix_conv_lookup", "from_uom", "to_uom", "product_id", "brand_id"),
    )

    product_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("products.id"))
    brand_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("brands.id"))
    category: Mapped[str | None] = mapped_column(String(10))
    diameter_mm: Mapped[int | None] = mapped_column(Integer)  # brand-level TMT charts are per diameter
    from_uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), nullable=False)
    to_uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), nullable=False)
    factor: Mapped[Decimal] = mapped_column(Factor, nullable=False)
    basis: Mapped[str] = mapped_column(String(12), default="STANDARD", nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, server_default=text("'2000-01-01'"), nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_note: Mapped[str | None] = mapped_column(String(200))


class CompanyProduct(Base):
    __tablename__ = "company_products"
    __table_args__ = (
        UniqueConstraint("company_id", "tally_guid"),
        enum_check("source", ["TALLY", "SEED", "DEMO"], "source"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id"), primary_key=True
    )
    tally_stock_item_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Products come from Tally stock items; ones not used in the ERP are switched off here.
    tally_guid: Mapped[str | None] = mapped_column(String(100))
    tally_parent: Mapped[str | None] = mapped_column(String(200))
    tally_base_unit: Mapped[str | None] = mapped_column(String(30))
    tally_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(10), default="TALLY", nullable=False)
    erp_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    product: Mapped["Product"] = relationship(lazy="joined")


# ---------------------------------------------------------------- dealers (sourced from Tally, decision D5)
DEALER_STATUS = ["ACTIVE", "BLOCKED", "INACTIVE"]
DEALER_CATEGORIES = ["DEALER", "RETAILER", "CONTRACTOR", "BUILDER", "INSTITUTION", "OTHER"]
CREDIT_POLICIES = ["STRICT", "WARN", "NONE"]


class Dealer(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "dealers"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        UniqueConstraint("company_id", "tally_ledger_guid"),
        enum_check("status", DEALER_STATUS, "status"),
        enum_check("category", DEALER_CATEGORIES, "category"),
        enum_check("credit_policy", CREDIT_POLICIES, "credit_policy"),
        enum_check("source", ["TALLY", "DEMO"], "source"),
    )

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    tally_ledger_name: Mapped[str | None] = mapped_column(String(200))
    tally_ledger_guid: Mapped[str | None] = mapped_column(String(100))
    tally_parent_group: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(10), default="TALLY", nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(200)), default=list, nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(15), default="DEALER", nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE", nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer)
    credit_limit: Mapped[Decimal | None] = mapped_column(Money)  # ERP override
    tally_credit_limit: Mapped[Decimal | None] = mapped_column(Money)
    tally_credit_period_days: Mapped[int | None] = mapped_column(Integer)
    # Ledger closing balance from Tally: positive = the dealer owes us (Dr). Read-only, refreshed on every sync.
    tally_balance: Mapped[Decimal | None] = mapped_column(Money)
    tally_balance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Contact details as typed in the Tally ledger (read-only; ERP contacts take priority for messages).
    tally_mobile: Mapped[str | None] = mapped_column(String(100))
    tally_phone: Mapped[str | None] = mapped_column(String(100))
    tally_contact_name: Mapped[str | None] = mapped_column(String(120))
    tally_address: Mapped[str | None] = mapped_column(String(500))
    allowed_overdue_days: Mapped[int | None] = mapped_column(Integer)
    credit_policy: Mapped[str] = mapped_column(String(10), default="STRICT", nullable=False)
    default_sales_exec_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id")
    )
    tally_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": version}

    contacts: Mapped[list["DealerContact"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    addresses: Mapped[list["DealerAddress"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class DealerContact(IdMixin, TimestampMixin, Base):
    __tablename__ = "dealer_contacts"

    dealer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("dealers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    role: Mapped[str | None] = mapped_column(String(60))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DealerAddress(IdMixin, TimestampMixin, Base):
    __tablename__ = "dealer_addresses"
    __table_args__ = (enum_check("kind", ["BILLING", "DELIVERY", "SITE"], "kind"),)

    dealer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("dealers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    line1: Mapped[str | None] = mapped_column(String(200))
    line2: Mapped[str | None] = mapped_column(String(200))
    area: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(80))
    state_code: Mapped[str | None] = mapped_column(String(2))
    pincode: Mapped[str | None] = mapped_column(String(6))
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DealerAssignment(IdMixin, TimestampMixin, Base):
    """History of sales/collection ownership. Exactly one open row (valid_to IS NULL) per dealer+kind."""

    __tablename__ = "dealer_assignments"
    __table_args__ = (
        enum_check("kind", ["SALES", "COLLECTION"], "kind"),
        Index(
            "uq_dealer_assignment_open",
            "dealer_id",
            "kind",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )

    dealer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("dealers.id"), index=True, nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)


class DealerRate(IdMixin, TimestampMixin, CompanyMixin, Base):
    """Customer-specific rate (decision D5). Most specific (with godown) wins; latest valid_from wins."""

    __tablename__ = "dealer_rates"
    __table_args__ = (
        CheckConstraint("rate > 0", name="rate_positive"),
        CheckConstraint("tolerance_pct >= 0 AND tolerance_pct <= 100", name="tolerance_range"),
        enum_check("source", ["MANUAL", "TALLY_LAST_SALE"], "source"),
        Index("ix_dealer_rate_lookup", "dealer_id", "product_id", "valid_from"),
    )

    dealer_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("dealers.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    godown_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("godowns.id"))
    uom: Mapped[str] = mapped_column(String(10), ForeignKey("uoms.code"), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False)
    tolerance_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="MANUAL", nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)


# ---------------------------------------------------------------- transport
class Transporter(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "transporters"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    phone: Mapped[str | None] = mapped_column(String(20))
    is_own_fleet: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


VEHICLE_TYPES = ["TRUCK_6W", "TRUCK_10W", "TRUCK_12W", "TRUCK_14W", "TRAILER", "TRACTOR", "PICKUP", "OTHER"]


class Vehicle(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        UniqueConstraint("company_id", "registration_no"),
        enum_check("vehicle_type", VEHICLE_TYPES, "vehicle_type"),
        enum_check("ownership", ["OWN", "ATTACHED", "MARKET"], "ownership"),
        enum_check("status", ["AVAILABLE", "ON_TRIP", "MAINTENANCE", "INACTIVE"], "status"),
        CheckConstraint("capacity_kg > 0", name="capacity_positive"),
    )

    registration_no: Mapped[str] = mapped_column(String(20), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(12), nullable=False)
    capacity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    transporter_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("transporters.id")
    )
    ownership: Mapped[str] = mapped_column(String(10), default="OWN", nullable=False)  # trucks are owned
    status: Mapped[str] = mapped_column(String(12), default="AVAILABLE", nullable=False)
    gps_device_id: Mapped[str | None] = mapped_column(String(60))


class Driver(IdMixin, TimestampMixin, CompanyMixin, Base):
    __tablename__ = "drivers"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    licence_no: Mapped[str | None] = mapped_column(String(30))
    user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    transporter_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("transporters.id")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
