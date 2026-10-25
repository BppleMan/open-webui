"""Persist cost snapshot totals alongside their immutable ledger boundary."""

from alembic import op
import sqlalchemy as sa

revision = 'oi0000000002'
down_revision = 'oi0000000001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('oi_cost_snapshot', sa.Column('totals', sa.JSON(), nullable=True))


def downgrade():
    raise RuntimeError('Keep accounting snapshots; restore a compatible OI build instead')
