"""Add OI identity, drafts, accounting and navigation records without changing history."""

import sqlalchemy as sa
from alembic import op

revision = 'oi0000000001'
down_revision = '0bd252b206fb'
branch_labels = None
depends_on = None


def upgrade():
    # Keep migration definitions independent from future ORM model changes.
    definitions = {
        'oi_connection': [
            ('id', sa.Text, {'primary_key': True}),
            ('owner_id', sa.Text, {}),
            ('name', sa.Text, {}),
            ('template', sa.Text, {}),
            ('protocol', sa.Text, {}),
            ('base_url', sa.Text, {}),
            ('credentials', sa.Text, {}),
            ('settings', sa.JSON, {}),
            ('shared', sa.Boolean, {}),
            ('enabled', sa.Boolean, {}),
            ('legacy_key', sa.Text, {'nullable': True, 'unique': True}),
            ('revision', sa.Integer, {}),
            ('created_at', sa.BigInteger, {}),
            ('updated_at', sa.BigInteger, {}),
        ],
        'oi_model_binding': [
            ('id', sa.Text, {'primary_key': True}),
            ('connection_id', sa.Text, {}),
            ('upstream_id', sa.Text, {}),
            ('protocol', sa.Text, {}),
            ('name', sa.Text, {}),
            ('enabled', sa.Boolean, {}),
            ('available', sa.Boolean, {}),
            ('capabilities', sa.JSON, {}),
            ('pricing', sa.JSON, {}),
            ('params', sa.JSON, {}),
            ('aliases', sa.JSON, {}),
            ('updated_at', sa.BigInteger, {}),
        ],
        'oi_chat_draft': [
            ('user_id', sa.Text, {'primary_key': True}),
            ('chat_id', sa.Text, {'primary_key': True}),
            ('revision', sa.Integer, {}),
            ('payload', sa.JSON, {}),
            ('mutation_id', sa.Text, {'nullable': True}),
            ('updated_at', sa.BigInteger, {}),
        ],
        'oi_chat_ledger': [
            ('chat_id', sa.Text, {'primary_key': True}),
            ('user_id', sa.Text, {}),
            ('sequence', sa.Integer, {}),
            ('inherited_snapshot_id', sa.Text, {'nullable': True}),
        ],
        'oi_generation': [
            ('id', sa.Text, {'primary_key': True}),
            ('user_id', sa.Text, {}),
            ('chat_id', sa.Text, {}),
            ('message_id', sa.Text, {'nullable': True}),
            ('request_id', sa.Text, {}),
            ('kind', sa.Text, {}),
            ('status', sa.Text, {}),
            ('created_at', sa.BigInteger, {}),
            ('completed_at', sa.BigInteger, {'nullable': True}),
        ],
        'oi_model_call': [
            ('id', sa.Text, {'primary_key': True}),
            ('generation_id', sa.Text, {}),
            ('chat_id', sa.Text, {}),
            ('sequence', sa.Integer, {}),
            ('binding_id', sa.Text, {'nullable': True}),
            ('connection_id', sa.Text, {'nullable': True}),
            ('model_id', sa.Text, {}),
            ('status', sa.Text, {}),
            ('usage', sa.JSON, {}),
            ('actual_usd', sa.Text, {'nullable': True}),
            ('estimated_usd', sa.Text, {'nullable': True}),
            ('pricing', sa.JSON, {}),
            ('source', sa.Text, {'nullable': True}),
            ('created_at', sa.BigInteger, {}),
            ('completed_at', sa.BigInteger, {'nullable': True}),
        ],
        'oi_cost_snapshot': [
            ('id', sa.Text, {'primary_key': True}),
            ('chat_id', sa.Text, {}),
            ('message_id', sa.Text, {'nullable': True}),
            ('generation_id', sa.Text, {'unique': True}),
            ('max_sequence', sa.Integer, {}),
            ('inherited_snapshot_id', sa.Text, {'nullable': True}),
            ('baseline_ids', sa.JSON, {}),
            ('created_at', sa.BigInteger, {}),
        ],
        'oi_import_cost_baseline': [
            ('id', sa.Text, {'primary_key': True}),
            ('chat_id', sa.Text, {}),
            ('batch_id', sa.Text, {}),
            ('actual_usd', sa.Text, {'nullable': True}),
            ('estimated_usd', sa.Text, {'nullable': True}),
            ('note', sa.Text, {}),
            ('revision', sa.Integer, {}),
            ('created_at', sa.BigInteger, {}),
            ('updated_at', sa.BigInteger, {}),
        ],
        'oi_chat_annotation': [
            ('user_id', sa.Text, {'primary_key': True}),
            ('chat_id', sa.Text, {'primary_key': True}),
            ('revision', sa.Integer, {}),
            ('data', sa.JSON, {}),
            ('updated_at', sa.BigInteger, {}),
        ],
        'oi_message_revision': [
            ('id', sa.Text, {'primary_key': True}),
            ('chat_id', sa.Text, {}),
            ('message_id', sa.Text, {}),
            ('user_id', sa.Text, {}),
            ('content', sa.JSON, {'nullable': True}),
            ('output', sa.JSON, {'nullable': True}),
            ('created_at', sa.BigInteger, {}),
        ],
    }
    unique = {
        'oi_model_binding': [('connection_id', 'upstream_id', 'protocol', 'uq_oi_binding')],
        'oi_generation': [('user_id', 'request_id', 'uq_oi_generation_request')],
        'oi_model_call': [('chat_id', 'sequence', 'uq_oi_call_sequence')],
        'oi_import_cost_baseline': [('chat_id', 'batch_id', 'uq_oi_import_batch')],
    }
    for table, columns in definitions.items():
        op.create_table(
            table,
            *(sa.Column(name, kind(), **({'nullable': False} | options)) for name, kind, options in columns),
            *(sa.UniqueConstraint(*entry[:-1], name=entry[-1]) for entry in unique.get(table, [])),
        )
        for name, _, options in columns:
            if name in {
                'chat_id',
                'user_id',
                'owner_id',
                'connection_id',
                'generation_id',
                'message_id',
            } and not options.get('primary_key'):
                op.create_index(f'ix_{table}_{name}', table, [name])


def downgrade():
    raise RuntimeError(
        'OI records may contain new drafts and charges. Restore a verified backup instead of dropping them.'
    )
