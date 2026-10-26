"""Stable browser/server transport and idempotent migration receipts."""

from alembic import op
import sqlalchemy as sa

revision = 'oi0000000005'
down_revision = 'oi0000000004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('oi_connection', sa.Column('transport', sa.Text(), nullable=False, server_default='server'))
    op.create_table(
        'oi_connection_migration',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('scope', sa.Text(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table('oi_connection_migration')
    op.drop_column('oi_connection', 'transport')
