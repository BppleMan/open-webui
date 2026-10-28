"""Serialize restores and retain idempotent JSON import receipts."""

from alembic import op
import sqlalchemy as sa

revision = 'oi0000000006'
down_revision = 'oi0000000005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'oi_user_state_lock',
        sa.Column('user_id', sa.Text(), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_table(
        'oi_import_receipt',
        sa.Column('user_id', sa.Text(), primary_key=True),
        sa.Column('import_id', sa.Text(), primary_key=True),
        sa.Column('digest', sa.Text(), nullable=False),
        sa.Column('chat_ids', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )

    op.create_table(
        'oi_archive_origin',
        sa.Column('user_id', sa.Text(), primary_key=True),
        sa.Column('kind', sa.Text(), primary_key=True),
        sa.Column('local_id', sa.Text(), primary_key=True),
        sa.Column('source_id', sa.Text(), nullable=False),
        sa.UniqueConstraint('user_id', 'kind', 'source_id', name='uq_oi_archive_origin'),
    )


def downgrade():
    op.drop_table('oi_archive_origin')
    op.drop_table('oi_import_receipt')
    op.drop_table('oi_user_state_lock')
