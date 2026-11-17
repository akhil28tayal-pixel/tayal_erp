"""Unit conversion engine.

Conversions are data (product_conversions) plus edges derived from product attributes:
  * cement: 1 BAG = bag_weight_kg KG
  * TMT:    1 PC  = length_m M        (the manufacturer chart supplies kg per metre: 1 M = x KG)
Global rows (MT→KG = 1000, QTL→KG = 100) apply to everything. For each (from, to) pair the most specific
scope wins: product > brand+diameter > brand > category > global, then the latest effective_from.
A path is found by BFS over edges and their inverses; the composite factor is frozen onto document lines.
"""

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.masters.models import Product, ProductConversion

BASE_QUANT = Decimal("0.000001")
FACTOR_QUANT = Decimal("0.00000001")


@dataclass
class Edge:
    from_uom: str
    to_uom: str
    factor: Decimal
    source: str  # conversion id or "derived:..."


@dataclass
class ConversionResult:
    product_id: uuid.UUID
    qty: Decimal
    from_uom: str
    to_uom: str
    result: Decimal
    factor: Decimal
    path: list[str] = field(default_factory=list)


def _specificity(c: ProductConversion, product: Product) -> int:
    """product > brand+diameter > brand > category+diameter (e.g. IS 1786 chart for all TMT) > category > global."""
    if c.product_id == product.id:
        return 5
    if c.brand_id is not None and c.brand_id == product.brand_id:
        return 4 if c.diameter_mm is not None else 3
    if c.category is not None and c.category == product.category:
        return 2 if c.diameter_mm is not None else 1
    return 0


def product_edges(db: Session, product: Product, on: date | None = None) -> list[Edge]:
    on = on or date.today()
    rows = db.execute(
        select(ProductConversion).where(
            ProductConversion.effective_from <= on,
            or_(ProductConversion.effective_to.is_(None), ProductConversion.effective_to >= on),
            or_(
                ProductConversion.product_id == product.id,
                and_(
                    ProductConversion.product_id.is_(None),
                    ProductConversion.brand_id == product.brand_id,
                    or_(
                        ProductConversion.diameter_mm.is_(None),
                        ProductConversion.diameter_mm == product.diameter_mm,
                    ),
                ),
                and_(
                    ProductConversion.product_id.is_(None),
                    ProductConversion.brand_id.is_(None),
                    or_(ProductConversion.category.is_(None), ProductConversion.category == product.category),
                    or_(
                        ProductConversion.diameter_mm.is_(None),
                        ProductConversion.diameter_mm == product.diameter_mm,
                    ),
                ),
            ),
        )
    ).scalars().all()

    best: dict[tuple[str, str], ProductConversion] = {}
    for c in rows:
        key = (c.from_uom, c.to_uom)
        cur = best.get(key)
        if cur is None or (_specificity(c, product), c.effective_from) > (
            _specificity(cur, product),
            cur.effective_from,
        ):
            best[key] = c
    edges = [Edge(c.from_uom, c.to_uom, Decimal(c.factor), str(c.id)) for c in best.values()]

    explicit = set(best)
    if product.category == "CEMENT" and product.bag_weight_kg and ("BAG", "KG") not in explicit:
        edges.append(Edge("BAG", "KG", Decimal(product.bag_weight_kg), "derived:bag_weight_kg"))
    if product.category == "TMT" and product.length_m and ("PC", "M") not in explicit:
        edges.append(Edge("PC", "M", Decimal(product.length_m), "derived:length_m"))
    return edges


def find_factor(edges: list[Edge], from_uom: str, to_uom: str) -> tuple[Decimal, list[str]] | None:
    if from_uom == to_uom:
        return Decimal(1), []
    graph: dict[str, list[tuple[str, Decimal, str]]] = {}
    with localcontext() as ctx:
        ctx.prec = 40
        for e in edges:
            graph.setdefault(e.from_uom, []).append((e.to_uom, e.factor, e.source))
            graph.setdefault(e.to_uom, []).append((e.from_uom, Decimal(1) / e.factor, f"inverse:{e.source}"))
        queue = deque([(from_uom, Decimal(1), [])])
        seen = {from_uom}
        while queue:
            uom, factor, path = queue.popleft()
            for nxt, f, src in graph.get(uom, []):
                if nxt in seen:
                    continue
                nf, npath = factor * f, [*path, src]
                if nxt == to_uom:
                    return nf, npath
                seen.add(nxt)
                queue.append((nxt, nf, npath))
    return None


def convert(db: Session, product: Product, qty: Decimal, from_uom: str, to_uom: str | None = None,
            on: date | None = None) -> ConversionResult:
    """Convert using a factor frozen to 8 dp, result to 6 dp. Same inputs → same output, always."""
    to_uom = to_uom or product.base_uom
    found = find_factor(product_edges(db, product, on), from_uom, to_uom)
    if found is None:
        raise AppError(
            422, "CONVERSION_NOT_FOUND",
            f"No conversion from {from_uom} to {to_uom} for product {product.code}",
        )
    factor, path = found
    frozen = factor.quantize(FACTOR_QUANT, rounding=ROUND_HALF_UP)
    if frozen == 0:
        raise AppError(422, "CONVERSION_NOT_FOUND", "Conversion factor rounds to zero")
    result = (Decimal(qty) * frozen).quantize(BASE_QUANT, rounding=ROUND_HALF_UP)
    return ConversionResult(product.id, Decimal(qty), from_uom, to_uom, result, frozen, path)
