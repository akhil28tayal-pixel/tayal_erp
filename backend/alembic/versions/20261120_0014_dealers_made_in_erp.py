"""dealers made in the ERP (booked before they were in Tally)

Revision ID: 0014
Revises: 0013
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f('ck_dealers_source'), 'dealers', type_='check')
    op.create_check_constraint(op.f('ck_dealers_source'), 'dealers', "source IN ('TALLY', 'DEMO', 'ERP')")
    op.add_column('dealers', sa.Column('tally_state', sa.String(length=60), nullable=True))
    op.add_column('dealers', sa.Column('tally_pincode', sa.String(length=6), nullable=True))


def downgrade() -> None:
    op.drop_column('dealers', 'tally_pincode')
    op.drop_column('dealers', 'tally_state')
    op.execute("UPDATE dealers SET source = 'TALLY' WHERE source = 'ERP'")
    op.drop_constraint(op.f('ck_dealers_source'), 'dealers', type_='check')
    op.create_check_constraint(op.f('ck_dealers_source'), 'dealers', "source IN ('TALLY', 'DEMO')")
