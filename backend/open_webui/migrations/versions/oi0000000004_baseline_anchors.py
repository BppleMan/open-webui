"""Allow non-generating cost anchors for imported or inherited history."""

from alembic import op
import sqlalchemy as sa

revision = 'oi0000000004'
down_revision = 'oi0000000003'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('oi_cost_snapshot') as batch:
        batch.alter_column('generation_id', existing_type=sa.Text(), nullable=True)


def downgrade():
    raise RuntimeError('Keep inherited cost anchors; restore a compatible OI build instead')
