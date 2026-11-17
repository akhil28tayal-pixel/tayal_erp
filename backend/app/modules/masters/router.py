import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.audit import audit_ctx, snapshot, write_audit
from app.core.db import get_db
from app.core.deps import Ctx, get_current_user, require
from app.core.errors import AppError, conflict, forbidden, not_found
from app.core.pagination import Page, PageParams, paginate
from app.modules.identity.models import User, UserRole
from app.modules.masters import service
from app.modules.masters.conversions import convert
from app.modules.masters.crud import get_scoped, register_crud
from app.modules.masters.models import (
    Brand,
    Company,
    CompanyProduct,
    Dealer,
    DealerAddress,
    DealerAssignment,
    DealerContact,
    DealerRate,
    Driver,
    Employee,
    Godown,
    InventoryOwner,
    Product,
    ProductConversion,
    ProductUnit,
    Transporter,
    Uom,
    Vehicle,
)
from app.modules.masters.schemas import (
    AssignmentBulkIn,
    AssignmentOut,
    BlockIn,
    BrandIn,
    BrandOut,
    BrandUpdate,
    CompanyOut,
    CompanyUpdate,
    ConversionIn,
    ConversionOut,
    ConvertIn,
    ConvertOut,
    DealerAddressIn,
    DealerAddressOut,
    DealerContactIn,
    DealerContactOut,
    DealerDetailOut,
    DealerOut,
    DealerRateIn,
    DealerRateOut,
    DealerUpdate,
    DriverIn,
    DriverOut,
    DriverUpdate,
    EmployeeIn,
    EmployeeOut,
    EmployeeUpdate,
    GodownIn,
    GodownOut,
    GodownUpdate,
    OwnerIn,
    OwnerOut,
    OwnerUpdate,
    ProductOut,
    ProductUnitIn,
    ProductUpdate,
    ResolvedRate,
    TransporterIn,
    TransporterOut,
    TransporterUpdate,
    VehicleIn,
    VehicleOut,
    VehicleUpdate,
)

router = APIRouter()


def _refs(**models):
    """Validator: referenced ids must exist in the caller's company."""

    def _check(db: Session, ctx: Ctx, data: dict) -> None:
        for field, model in models.items():
            ref = data.get(field)
            if ref is None:
                continue
            obj = db.get(model, ref)
            if obj is None or getattr(obj, "company_id", ctx.company_id) != ctx.company_id:
                raise AppError(422, "VALIDATION_ERROR", f"Unknown {field}",
                               errors=[{"field": field, "code": "unknown_reference", "message": "not found"}])

    return _check


# ------------------------------------------------------------------ companies
@router.get("/companies", response_model=list[CompanyOut], tags=["companies"])
def my_companies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.execute(
        select(Company)
        .where(Company.id.in_(select(UserRole.company_id).where(UserRole.user_id == user.id)))
        .order_by(Company.code)
    ).scalars().all()


@router.patch("/companies/current", response_model=CompanyOut, tags=["companies"])
def update_company(body: CompanyUpdate, ctx: Ctx = Depends(require("admin.settings")),
                   db: Session = Depends(get_db)):
    company = db.get(Company, ctx.company_id)
    before = snapshot(company)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(company, k, v)
    audit_ctx(db, ctx, "company.update", company, before)
    db.commit()
    return company


# ------------------------------------------------------------------ simple masters
def _godown_scope(stmt, ctx: Ctx):
    return stmt.where(Godown.id.in_(ctx.godown_ids)) if ctx.godown_ids is not None else stmt


register_crud(router, path="/employees", model=Employee, entity="employee", create_schema=EmployeeIn,
              update_schema=EmployeeUpdate, out_schema=EmployeeOut, view_perm="masters.view",
              manage_perm="admin.users", sortable={"code": Employee.code, "full_name": Employee.full_name},
              default_sort=Employee.full_name, search_cols=(Employee.full_name, Employee.code, Employee.phone),
              validate=_refs(reports_to_id=Employee))
# Godowns come from Tally (no manual create); list is hand-written below to support the ERP on/off filter.
register_crud(router, path="/godowns", model=Godown, entity="godown", create_schema=GodownIn,
              update_schema=GodownUpdate, out_schema=GodownOut, view_perm="masters.view",
              manage_perm="masters.godowns_manage", sortable={"code": Godown.code, "name": Godown.name},
              default_sort=Godown.code, scope_filter=_godown_scope,
              validate=_refs(supervisor_employee_id=Employee), allow_create=False, allow_list=False)


@router.get("/godowns", response_model=Page[GodownOut], tags=["godowns"])
def list_godowns(params: PageParams = Depends(), q: str | None = None, erp_enabled: bool | None = None,
                 include_deleted: bool = False, source: str | None = None,
                 ctx: Ctx = Depends(require("masters.view")), db: Session = Depends(get_db)):
    """Operational screens pass erp_enabled=true (dispatch also source=TALLY); the godown master shows everything."""
    stmt = select(Godown).where(Godown.company_id == ctx.company_id)
    if source:
        stmt = stmt.where(Godown.source == source)
    if q:
        stmt = stmt.where(or_(Godown.name.ilike(f"%{q}%"), Godown.code.ilike(f"%{q}%")))
    if erp_enabled is not None:
        stmt = stmt.where(Godown.erp_enabled.is_(erp_enabled))
    if not include_deleted:
        stmt = stmt.where(Godown.tally_deleted.is_(False))
    stmt = _godown_scope(stmt, ctx)
    return paginate(db, stmt, params, {"code": Godown.code, "name": Godown.name}, Godown.name)


def _toggle_godown(godown_id: uuid.UUID, enabled: bool, ctx: Ctx, db: Session) -> Godown:
    g = get_scoped(db, Godown, godown_id, ctx, "Godown")
    if enabled and g.tally_deleted:
        raise AppError(422, "GODOWN_DELETED_IN_TALLY", "This godown no longer exists in Tally")
    before = snapshot(g)
    g.erp_enabled = enabled
    g.updated_by = ctx.user_id
    db.flush()
    audit_ctx(db, ctx, "godown.erp_enable" if enabled else "godown.erp_disable", g, before)
    db.commit()
    return g


@router.post("/godowns/{godown_id}/erp-enable", response_model=GodownOut, tags=["godowns"])
def enable_godown(godown_id: uuid.UUID, ctx: Ctx = Depends(require("masters.godowns_manage")),
                  db: Session = Depends(get_db)):
    return _toggle_godown(godown_id, True, ctx, db)


@router.post("/godowns/{godown_id}/erp-disable", response_model=GodownOut, tags=["godowns"])
def disable_godown(godown_id: uuid.UUID, ctx: Ctx = Depends(require("masters.godowns_manage")),
                   db: Session = Depends(get_db)):
    """Switch off a Tally godown that is not used in the ERP. Stock checks for it are added in Step 4."""
    return _toggle_godown(godown_id, False, ctx, db)
register_crud(router, path="/inventory-owners", model=InventoryOwner, entity="inventory_owner",
              create_schema=OwnerIn, update_schema=OwnerUpdate, out_schema=OwnerOut,
              view_perm="masters.view", manage_perm="masters.godowns_manage",
              sortable={"name": InventoryOwner.name}, default_sort=InventoryOwner.name)
register_crud(router, path="/transporters", model=Transporter, entity="transporter",
              create_schema=TransporterIn, update_schema=TransporterUpdate, out_schema=TransporterOut,
              view_perm="masters.view", manage_perm="masters.transport_manage",
              sortable={"name": Transporter.name}, default_sort=Transporter.name, search_cols=(Transporter.name,))
register_crud(router, path="/vehicles", model=Vehicle, entity="vehicle", create_schema=VehicleIn,
              update_schema=VehicleUpdate, out_schema=VehicleOut, view_perm="masters.view",
              manage_perm="masters.transport_manage",
              sortable={"registration_no": Vehicle.registration_no, "capacity_kg": Vehicle.capacity_kg},
              default_sort=Vehicle.registration_no, search_cols=(Vehicle.registration_no,),
              validate=_refs(transporter_id=Transporter))
register_crud(router, path="/drivers", model=Driver, entity="driver", create_schema=DriverIn,
              update_schema=DriverUpdate, out_schema=DriverOut, view_perm="masters.view",
              manage_perm="masters.transport_manage", sortable={"name": Driver.name}, default_sort=Driver.name,
              search_cols=(Driver.name, Driver.phone), validate=_refs(transporter_id=Transporter))
register_crud(router, path="/brands", model=Brand, entity="brand", create_schema=BrandIn,
              update_schema=BrandUpdate, out_schema=BrandOut, view_perm="masters.view",
              manage_perm="masters.products_manage", sortable={"code": Brand.code, "name": Brand.name},
              default_sort=Brand.name, search_cols=(Brand.name, Brand.code), company_scoped=False)


# ------------------------------------------------------------------ products & conversions
@router.get("/uoms", tags=["products"])
def list_uoms(ctx: Ctx = Depends(require("masters.view")), db: Session = Depends(get_db)):
    return [{"code": u.code, "name": u.name, "kind": u.kind}
            for u in db.execute(select(Uom).order_by(Uom.code)).scalars()]


def _product_out(db: Session, p: Product, cp: CompanyProduct, general_on: bool | None = None) -> dict:
    from app.modules.masters.tally_products import enable_blocked_reason

    return {**ProductOut.model_validate(p).model_dump(), "erp_enabled": cp.erp_enabled, "source": cp.source,
            "tally_name": cp.tally_stock_item_name, "tally_parent": cp.tally_parent,
            "tally_base_unit": cp.tally_base_unit, "tally_deleted": cp.tally_deleted,
            "setup_missing": p.missing_setup(),
            "enable_blocked_reason": None if cp.erp_enabled else enable_blocked_reason(db, p, cp, general_on)}


def _company_product(db: Session, ctx: Ctx, product_id: uuid.UUID) -> tuple[Product, CompanyProduct]:
    cp = db.get(CompanyProduct, (ctx.company_id, product_id))
    if cp is None:
        raise not_found("Product")
    return cp.product, cp


@router.get("/products", response_model=Page[ProductOut], tags=["products"])
def list_products(params: PageParams = Depends(), q: str | None = None, brand_id: uuid.UUID | None = None,
                  category: str | None = None, erp_enabled: bool | None = None, needs_setup: bool | None = None,
                  include_deleted: bool = False, ctx: Ctx = Depends(require("masters.view")),
                  db: Session = Depends(get_db)):
    """Products of this company (from Tally). Order and stock screens pass erp_enabled=true."""
    stmt = (select(Product, CompanyProduct).join(CompanyProduct, CompanyProduct.product_id == Product.id)
            .where(CompanyProduct.company_id == ctx.company_id))
    if q:
        stmt = stmt.where(or_(Product.name.ilike(f"%{q}%"), Product.code.ilike(f"%{q}%"),
                              CompanyProduct.tally_stock_item_name.ilike(f"%{q}%")))
    if brand_id:
        stmt = stmt.where(Product.brand_id == brand_id)
    if category:
        stmt = stmt.where(Product.category == category)
    if erp_enabled is not None:
        stmt = stmt.where(CompanyProduct.erp_enabled.is_(erp_enabled))
    if not include_deleted:
        stmt = stmt.where(CompanyProduct.tally_deleted.is_(False))
    if needs_setup is not None:
        incomplete = or_(Product.category == "OTHER",
                         Product.brand_id.is_(None) & (Product.category != "GENERAL"),
                         (Product.category == "CEMENT") & Product.bag_weight_kg.is_(None),
                         (Product.category == "TMT") & Product.diameter_mm.is_(None))
        stmt = stmt.where(incomplete if needs_setup else ~incomplete)
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    order = {"name": Product.name, "code": Product.code}
    sort_cols = []
    for part in (params.sort or "name").split(","):
        name = part.lstrip("-")
        if name not in order:
            raise AppError(422, "VALIDATION_ERROR", f"Cannot sort by '{name}'")
        sort_cols.append(order[name].desc() if part.startswith("-") else order[name].asc())
    rows = db.execute(stmt.order_by(*sort_cols).offset((params.page - 1) * params.page_size)
                      .limit(params.page_size)).unique().all()
    from app.modules.masters.tally_products import other_materials_enabled

    general_on = other_materials_enabled(db, ctx.company_id)
    return {"items": [_product_out(db, p, cp, general_on) for p, cp in rows], "page": params.page,
            "page_size": params.page_size, "total": total}


def _set_units(db: Session, product: Product, units) -> None:
    db.execute(delete(ProductUnit).where(ProductUnit.product_id == product.id))
    db.flush()
    seen = {product.base_uom}
    db.add(ProductUnit(product_id=product.id, uom=product.base_uom, is_sellable=True,
                       is_default_entry=not any(u.is_default_entry for u in units)))
    for u in units:
        if u.uom in seen:
            continue
        seen.add(u.uom)
        db.add(ProductUnit(product_id=product.id, **u.model_dump()))


@router.get("/products/{product_id}", response_model=ProductOut, tags=["products"])
def get_product(product_id: uuid.UUID, ctx: Ctx = Depends(require("masters.view")),
                db: Session = Depends(get_db)):
    return _product_out(db, *_company_product(db, ctx, product_id))


CATEGORY_UNITS = {"CEMENT": ("BAG", ["BAG", "MT"]), "TMT": ("KG", ["KG", "MT", "PC", "BDL"])}


@router.patch("/products/{product_id}", response_model=ProductOut, tags=["products"])
def update_product(product_id: uuid.UUID, body: ProductUpdate,
                   ctx: Ctx = Depends(require("masters.products_manage")), db: Session = Depends(get_db)):
    """Complete or correct a product's setup. Changing the category resets its stock unit (bags for cement,
    kg for TMT); that is only safe before it has stock or orders, which Step 3/4 will enforce."""
    p, cp = _company_product(db, ctx, product_id)
    before = snapshot(p)
    data = body.model_dump(exclude_unset=True, exclude={"units"})
    if data.get("brand_id"):
        brand = db.get(Brand, data["brand_id"])
        if brand is None:
            raise AppError(422, "VALIDATION_ERROR", "Unknown brand")
        if brand.category != data.get("category", p.category) and data.get("category", p.category) != "OTHER":
            raise AppError(422, "VALIDATION_ERROR", f"{brand.name} is a {brand.category.lower()} brand")
    new_cat = data.get("category", p.category)
    if new_cat == "CEMENT" and not data.get("bag_weight_kg", p.bag_weight_kg):
        data["bag_weight_kg"] = Decimal("50")
    if new_cat == "TMT" and not data.get("diameter_mm", p.diameter_mm):
        raise AppError(422, "VALIDATION_ERROR", "Enter the bar diameter for a TMT product",
                       errors=[{"field": "diameter_mm", "code": "required", "message": "Required for TMT"}])
    if new_cat == "TMT" and not p.length_m and "length_m" not in data:
        data["length_m"] = Decimal("12")
    if new_cat == "GENERAL":
        unit = data.get("base_uom") or p.base_uom
        if db.get(Uom, unit) is None:
            raise AppError(422, "VALIDATION_ERROR", f"Unknown unit {unit}",
                           errors=[{"field": "base_uom", "code": "unknown", "message": "Unknown unit"}])
        data["base_uom"] = unit
    elif "base_uom" in data:
        data.pop("base_uom")  # cement is always stocked in bags, TMT in kg
    for k, v in data.items():
        setattr(p, k, v)
    if "category" in data and new_cat in CATEGORY_UNITS:
        p.base_uom = CATEGORY_UNITS[new_cat][0]
        db.flush()
        _set_units(db, p, [ProductUnitIn(uom=u) for u in CATEGORY_UNITS[new_cat][1]])
    elif new_cat == "GENERAL" and ("category" in data or "base_uom" in data):
        db.flush()
        _set_units(db, p, [])
    elif body.units is not None:
        _set_units(db, p, body.units)
    p.updated_by = ctx.user_id
    db.flush()
    db.refresh(p)
    audit_ctx(db, ctx, "product.update", p, before)
    db.commit()
    db.refresh(p)
    return _product_out(db, p, cp)


def _toggle_product(product_id: uuid.UUID, enabled: bool, ctx: Ctx, db: Session) -> dict:
    from app.modules.masters.tally_products import enable_blocked_reason

    p, cp = _company_product(db, ctx, product_id)
    if enabled and cp.tally_deleted:
        raise AppError(422, "PRODUCT_DELETED_IN_TALLY", "This item no longer exists in Tally")
    if enabled and p.missing_setup():
        raise AppError(422, "PRODUCT_NEEDS_SETUP", f"Set the {', '.join(p.missing_setup())} first")
    if enabled and (reason := enable_blocked_reason(db, p, cp)):
        raise AppError(422, "PRODUCT_NEXT_PHASE", reason)
    before = {"erp_enabled": cp.erp_enabled}
    cp.erp_enabled = enabled
    from app.core.audit import write_audit

    write_audit(db, action="product.erp_enable" if enabled else "product.erp_disable", entity_type="products",
                entity_id=p.id, actor_id=ctx.user_id, company_id=ctx.company_id, before=before,
                after={"erp_enabled": enabled})
    db.commit()
    return _product_out(db, p, cp)


@router.post("/products/{product_id}/erp-enable", response_model=ProductOut, tags=["products"])
def enable_product(product_id: uuid.UUID, ctx: Ctx = Depends(require("masters.products_manage")),
                   db: Session = Depends(get_db)):
    return _toggle_product(product_id, True, ctx, db)


@router.post("/products/{product_id}/erp-disable", response_model=ProductOut, tags=["products"])
def disable_product(product_id: uuid.UUID, ctx: Ctx = Depends(require("masters.products_manage")),
                    db: Session = Depends(get_db)):
    return _toggle_product(product_id, False, ctx, db)


@router.get("/products/conversions/list", response_model=list[ConversionOut], tags=["products"])
def list_conversions(product_id: uuid.UUID | None = None, brand_id: uuid.UUID | None = None,
                     ctx: Ctx = Depends(require("masters.view")), db: Session = Depends(get_db)):
    stmt = select(ProductConversion).order_by(ProductConversion.from_uom, ProductConversion.to_uom)
    if product_id:
        stmt = stmt.where(ProductConversion.product_id == product_id)
    if brand_id:
        stmt = stmt.where(ProductConversion.brand_id == brand_id)
    return db.execute(stmt).scalars().all()


@router.post("/products/conversions", response_model=ConversionOut, status_code=201, tags=["products"])
def create_conversion(body: ConversionIn, ctx: Ctx = Depends(require("masters.products_manage")),
                      db: Session = Depends(get_db)):
    """Conversions are effective-dated and never edited in place: to change a chart, end-date the old row
    and add a new one. Document lines keep the factor that applied when they were entered."""
    for uom in (body.from_uom, body.to_uom):
        if db.get(Uom, uom) is None:
            raise AppError(422, "VALIDATION_ERROR", f"Unknown unit {uom}")
    conv = ProductConversion(**body.model_dump(), created_by=ctx.user_id)
    db.add(conv)
    db.flush()
    audit_ctx(db, ctx, "conversion.create", conv)
    db.commit()
    return conv


@router.post("/products/conversions/{conversion_id}/end", response_model=ConversionOut, tags=["products"])
def end_conversion(conversion_id: uuid.UUID, effective_to: date,
                   ctx: Ctx = Depends(require("masters.products_manage")), db: Session = Depends(get_db)):
    conv = db.get(ProductConversion, conversion_id)
    if conv is None:
        raise not_found("Conversion")
    if effective_to < conv.effective_from:
        raise AppError(422, "VALIDATION_ERROR", "effective_to is before effective_from")
    before = snapshot(conv)
    conv.effective_to = effective_to
    audit_ctx(db, ctx, "conversion.end", conv, before)
    db.commit()
    return conv


@router.post("/products/convert", response_model=ConvertOut, tags=["products"])
def convert_qty(body: ConvertIn, ctx: Ctx = Depends(require("masters.view")), db: Session = Depends(get_db)):
    p = db.get(Product, body.product_id)
    if p is None:
        raise not_found("Product")
    r = convert(db, p, body.qty, body.from_uom, body.to_uom, body.on)
    return ConvertOut(product_id=p.id, qty=r.qty, from_uom=r.from_uom, to_uom=r.to_uom, result=r.result,
                      factor=r.factor, path=r.path)


# ------------------------------------------------------------------ dealers (Tally-sourced, D5)
def _dealer_visible(stmt, ctx: Ctx):
    if ctx.has("dealers.view_all"):
        return stmt
    return stmt.where(Dealer.id.in_(service.assigned_dealer_ids_subquery(ctx.user.employee_id)))


def _get_dealer(db: Session, ctx: Ctx, dealer_id: uuid.UUID) -> Dealer:
    dealer = get_scoped(db, Dealer, dealer_id, ctx, "Dealer")
    if db.execute(_dealer_visible(select(Dealer.id).where(Dealer.id == dealer_id), ctx)).first() is None:
        raise not_found("Dealer")
    return dealer


@router.get("/dealers", response_model=Page[DealerOut], tags=["dealers"])
def list_dealers(params: PageParams = Depends(), q: str | None = None, status: str | None = None,
                 employee_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("masters.view")),
                 db: Session = Depends(get_db)):
    stmt = select(Dealer).where(Dealer.company_id == ctx.company_id)
    if q:
        stmt = stmt.where(service.dealer_search(q))
    if status:
        stmt = stmt.where(Dealer.status == status)
    if employee_id:
        stmt = stmt.where(Dealer.id.in_(service.assigned_dealer_ids_subquery(employee_id)))
    stmt = _dealer_visible(stmt, ctx)
    return paginate(db, stmt, params, {"name": Dealer.name, "code": Dealer.code}, Dealer.name)


@router.get("/dealers/{dealer_id}", response_model=DealerDetailOut, tags=["dealers"])
def get_dealer(dealer_id: uuid.UUID, ctx: Ctx = Depends(require("masters.view")), db: Session = Depends(get_db)):
    return _get_dealer(db, ctx, dealer_id)


@router.patch("/dealers/{dealer_id}", response_model=DealerOut, tags=["dealers"])
def update_dealer(dealer_id: uuid.UUID, body: DealerUpdate,
                  ctx: Ctx = Depends(require("masters.dealers_manage")), db: Session = Depends(get_db)):
    dealer = _get_dealer(db, ctx, dealer_id)
    if body.version != dealer.version:
        raise conflict("VERSION_CONFLICT", "Dealer was changed by someone else; reload and retry",
                       current_version=dealer.version)
    data = body.model_dump(exclude_unset=True, exclude={"version"})
    if data.get("default_sales_exec_id"):
        _refs(default_sales_exec_id=Employee)(db, ctx, data)
    before = snapshot(dealer)
    for k, v in data.items():
        setattr(dealer, k, v)
    dealer.updated_by = ctx.user_id
    try:
        db.flush()
    except StaleDataError:
        raise conflict("VERSION_CONFLICT", "Dealer was changed by someone else; reload and retry") from None
    audit_ctx(db, ctx, "dealer.update", dealer, before)
    db.commit()
    return dealer


@router.post("/dealers/{dealer_id}/block", response_model=DealerOut, tags=["dealers"])
def block_dealer(dealer_id: uuid.UUID, body: BlockIn, ctx: Ctx = Depends(require("masters.dealers_manage")),
                 db: Session = Depends(get_db)):
    dealer = _get_dealer(db, ctx, dealer_id)
    before = snapshot(dealer)
    dealer.status, dealer.blocked_reason = "BLOCKED", body.reason
    db.flush()
    audit_ctx(db, ctx, "dealer.block", dealer, before)
    db.commit()
    return dealer


@router.post("/dealers/{dealer_id}/unblock", response_model=DealerOut, tags=["dealers"])
def unblock_dealer(dealer_id: uuid.UUID, ctx: Ctx = Depends(require("masters.dealers_manage")),
                   db: Session = Depends(get_db)):
    dealer = _get_dealer(db, ctx, dealer_id)
    if dealer.tally_deleted:
        raise AppError(422, "DEALER_DELETED_IN_TALLY", "Ledger no longer exists in Tally")
    before = snapshot(dealer)
    dealer.status, dealer.blocked_reason = "ACTIVE", None
    db.flush()
    audit_ctx(db, ctx, "dealer.unblock", dealer, before)
    db.commit()
    return dealer


def _contact_ctx(ctx: Ctx = Depends(require())) -> Ctx:
    """Office staff who manage dealers, and collection staff who get numbers in the field, may keep contacts."""
    if not (ctx.has("masters.dealers_manage") or ctx.has("collections.followup")):
        raise forbidden("Missing permission: masters.dealers_manage")
    return ctx


def _clean_contact(db: Session, dealer_id: uuid.UUID, body: DealerContactIn, keep_id: uuid.UUID | None = None) -> dict:
    from app.modules.collections.service import normalize_mobile

    data = body.model_dump()
    data["name"] = data["name"].strip()
    if data.get("phone"):
        mobile = normalize_mobile(data["phone"])
        if mobile is None:
            raise AppError(422, "VALIDATION_ERROR", "Enter a 10-digit mobile number", field="phone")
        data["phone"] = mobile[2:]  # stored as 10 digits; +91 is added when messaging
        dup = db.execute(select(DealerContact.id).where(DealerContact.dealer_id == dealer_id,
                                                        DealerContact.phone == data["phone"],
                                                        DealerContact.id != keep_id)).first()
        if dup:
            raise AppError(409, "CONFLICT", "This number is already saved for this dealer")
    if data.get("is_primary"):  # one primary (WhatsApp) number per dealer
        db.execute(update(DealerContact).where(DealerContact.dealer_id == dealer_id, DealerContact.id != keep_id)
                   .values(is_primary=False))
    return data


@router.post("/dealers/{dealer_id}/contacts", response_model=DealerContactOut, status_code=201, tags=["dealers"])
def add_contact(dealer_id: uuid.UUID, body: DealerContactIn, ctx: Ctx = Depends(_contact_ctx),
                db: Session = Depends(get_db)):
    """Add a contact / mobile number. The primary contact's mobile is used for WhatsApp messages."""
    _get_dealer(db, ctx, dealer_id)
    data = _clean_contact(db, dealer_id, body)
    if not db.execute(select(DealerContact.id).where(DealerContact.dealer_id == dealer_id)).first():
        data["is_primary"] = True  # the first number becomes the one messages go to
    c = DealerContact(dealer_id=dealer_id, **data, created_by=ctx.user_id)
    db.add(c)
    db.flush()
    audit_ctx(db, ctx, "dealer.contact_add", c)
    db.commit()
    return c


def _get_contact(db: Session, ctx: Ctx, dealer_id: uuid.UUID, contact_id: uuid.UUID) -> DealerContact:
    _get_dealer(db, ctx, dealer_id)
    c = db.get(DealerContact, contact_id)
    if c is None or c.dealer_id != dealer_id:
        raise not_found("Contact")
    return c


@router.put("/dealers/{dealer_id}/contacts/{contact_id}", response_model=DealerContactOut, tags=["dealers"])
def update_contact(dealer_id: uuid.UUID, contact_id: uuid.UUID, body: DealerContactIn,
                   ctx: Ctx = Depends(_contact_ctx), db: Session = Depends(get_db)):
    c = _get_contact(db, ctx, dealer_id, contact_id)
    before = snapshot(c)
    for k, v in _clean_contact(db, dealer_id, body, keep_id=c.id).items():
        setattr(c, k, v)
    c.updated_by = ctx.user_id
    db.flush()
    audit_ctx(db, ctx, "dealer.contact_update", c, before)
    db.commit()
    return c


@router.delete("/dealers/{dealer_id}/contacts/{contact_id}", status_code=204, tags=["dealers"])
def delete_contact(dealer_id: uuid.UUID, contact_id: uuid.UUID, ctx: Ctx = Depends(_contact_ctx),
                   db: Session = Depends(get_db)):
    c = _get_contact(db, ctx, dealer_id, contact_id)
    before = snapshot(c)
    was_primary = c.is_primary
    db.delete(c)
    db.flush()
    if was_primary:  # hand "primary" to the next number, if any
        nxt = db.execute(select(DealerContact).where(DealerContact.dealer_id == dealer_id)
                         .order_by(DealerContact.created_at)).scalars().first()
        if nxt:
            nxt.is_primary = True
    write_audit(db, action="dealer.contact_delete", entity_type="dealer_contacts", entity_id=contact_id,
                actor_id=ctx.user_id, company_id=ctx.company_id, before=before)
    db.commit()


@router.post("/dealers/{dealer_id}/addresses", response_model=DealerAddressOut, status_code=201,
             tags=["dealers"])
def add_address(dealer_id: uuid.UUID, body: DealerAddressIn,
                ctx: Ctx = Depends(require("masters.dealers_manage")), db: Session = Depends(get_db)):
    _get_dealer(db, ctx, dealer_id)
    a = DealerAddress(dealer_id=dealer_id, **body.model_dump(), created_by=ctx.user_id)
    db.add(a)
    db.flush()
    audit_ctx(db, ctx, "dealer.address_add", a)
    db.commit()
    return a


@router.post("/dealers/assignments/bulk", response_model=list[AssignmentOut], tags=["dealers"])
def bulk_assign(body: AssignmentBulkIn, ctx: Ctx = Depends(require("collections.assign")),
                db: Session = Depends(get_db)):
    if body.kind == "SALES" and not ctx.has("masters.dealers_manage"):
        raise AppError(403, "FORBIDDEN", "Missing permission: masters.dealers_manage")
    out = service.assign_dealers(db, ctx.company_id, body.dealer_ids, body.employee_id, body.kind,
                                 body.reason, ctx.user_id, body.effective_from)
    db.commit()
    return out


@router.get("/dealers/{dealer_id}/assignments", response_model=list[AssignmentOut], tags=["dealers"])
def assignment_history(dealer_id: uuid.UUID, ctx: Ctx = Depends(require("masters.view")),
                       db: Session = Depends(get_db)):
    _get_dealer(db, ctx, dealer_id)
    return db.execute(
        select(DealerAssignment).where(DealerAssignment.dealer_id == dealer_id)
        .order_by(DealerAssignment.valid_from.desc(), DealerAssignment.created_at.desc())
    ).scalars().all()


# ------------------------------------------------------------------ customer-specific rates
@router.get("/dealers/{dealer_id}/rates", response_model=list[DealerRateOut], tags=["dealer rates"])
def list_rates(dealer_id: uuid.UUID, ctx: Ctx = Depends(require("masters.view")), db: Session = Depends(get_db)):
    _get_dealer(db, ctx, dealer_id)
    return db.execute(
        select(DealerRate).where(DealerRate.dealer_id == dealer_id)
        .order_by(DealerRate.product_id, DealerRate.valid_from.desc())
    ).scalars().all()


@router.post("/dealers/{dealer_id}/rates", response_model=DealerRateOut, status_code=201, tags=["dealer rates"])
def add_rate(dealer_id: uuid.UUID, body: DealerRateIn, ctx: Ctx = Depends(require("masters.prices_manage")),
             db: Session = Depends(get_db)):
    _get_dealer(db, ctx, dealer_id)
    cp = db.get(CompanyProduct, (ctx.company_id, body.product_id))
    if cp is None:
        raise AppError(422, "VALIDATION_ERROR", "Unknown product")
    if not cp.erp_enabled:
        raise AppError(422, "PRODUCT_SWITCHED_OFF", "This product is switched off in the ERP")
    product = cp.product
    if body.uom not in {u.uom for u in product.units} | {product.base_uom}:
        raise AppError(422, "VALIDATION_ERROR", f"{body.uom} is not a unit of {product.code}")
    if body.godown_id:
        get_scoped(db, Godown, body.godown_id, ctx, "Godown")
    same_scope = [
        DealerRate.dealer_id == dealer_id, DealerRate.product_id == body.product_id,
        DealerRate.godown_id.is_(None) if body.godown_id is None else DealerRate.godown_id == body.godown_id,
        DealerRate.valid_to.is_(None),
    ]
    # Same-day re-entry is a correction of today's rate: update it (the audit log keeps the old value).
    same_day = db.execute(
        select(DealerRate).where(*same_scope, DealerRate.valid_from == body.valid_from)
    ).scalar_one_or_none()
    if same_day is not None:
        before = snapshot(same_day)
        for k in ("uom", "rate", "tolerance_pct", "valid_to"):
            setattr(same_day, k, getattr(body, k))
        same_day.updated_by = ctx.user_id
        db.flush()
        db.refresh(same_day)
        audit_ctx(db, ctx, "dealer_rate.correct", same_day, before)
        db.commit()
        return same_day
    # Otherwise close the currently open rate for the same scope so history stays linear.
    for r in db.execute(select(DealerRate).where(*same_scope, DealerRate.valid_from < body.valid_from)).scalars():
        r.valid_to = body.valid_from
    rate = DealerRate(company_id=ctx.company_id, dealer_id=dealer_id, source="MANUAL", **body.model_dump(),
                      created_by=ctx.user_id)
    db.add(rate)
    db.flush()
    audit_ctx(db, ctx, "dealer_rate.create", rate)
    db.commit()
    return rate


@router.get("/dealers/{dealer_id}/rates/resolve", response_model=ResolvedRate | None, tags=["dealer rates"])
def resolve(dealer_id: uuid.UUID, product_id: uuid.UUID, godown_id: uuid.UUID | None = None,
            on: date | None = None, ctx: Ctx = Depends(require("masters.view")), db: Session = Depends(get_db)):
    _get_dealer(db, ctx, dealer_id)
    return service.resolve_rate(db, ctx.company_id, dealer_id, product_id, godown_id, on)
