"""Other building materials (GENERAL category) and their units

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_products_category"), "products", type_="check")
    op.create_check_constraint("category", "products", "category IN ('CEMENT','TMT','GENERAL','OTHER')")
    op.drop_constraint(op.f("ck_uoms_kind"), "uoms", type_="check")
    op.create_check_constraint("kind", "uoms", "kind IN ('COUNT','WEIGHT','LENGTH','VOLUME','LOAD')")


def downgrade() -> None:
    op.execute("UPDATE products SET category = 'OTHER' WHERE category = 'GENERAL'")
    op.drop_constraint(op.f("ck_products_category"), "products", type_="check")
    op.create_check_constraint("category", "products", "category IN ('CEMENT','TMT','OTHER')")
    op.execute("DELETE FROM uoms WHERE kind IN ('VOLUME','LOAD') AND code NOT IN (SELECT base_uom FROM products)")
    op.drop_constraint(op.f("ck_uoms_kind"), "uoms", type_="check")
    op.create_check_constraint("kind", "uoms", "kind IN ('COUNT','WEIGHT','LENGTH')")
