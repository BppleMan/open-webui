"""Stable model identities with inherited protocols and persisted service routing."""

from alembic import op
import sqlalchemy as sa

revision = 'oi0000000007'
down_revision = 'oi0000000006'
branch_labels = None
depends_on = None


def upgrade():
    from open_webui.utils.oi.endpoints import normalize_service_address, api_base
    from types import SimpleNamespace
    from open_webui.utils.oi.protocol_registry import canonical_protocol

    with op.batch_alter_table('oi_model_binding') as batch:
        batch.add_column(sa.Column('protocol_override', sa.Text(), nullable=True))
        batch.drop_constraint('uq_oi_binding', type_='unique')
        batch.create_index('ix_oi_binding_provider', ['connection_id', 'upstream_id'])
    connection = sa.table(
        'oi_connection',
        sa.column('id'),
        sa.column('template'),
        sa.column('protocol'),
        sa.column('base_url'),
        sa.column('settings', sa.JSON),
    )
    binding = sa.table(
        'oi_model_binding',
        sa.column('id'),
        sa.column('connection_id'),
        sa.column('protocol'),
        sa.column('protocol_override'),
    )
    db = op.get_bind()
    for row in db.execute(sa.select(connection)).mappings().all():
        mode = canonical_protocol(row['protocol'])
        address, settings = normalize_service_address(
            row['base_url'], row['settings'], row['protocol'], row['template'], legacy=True
        )
        models = db.execute(sa.select(binding).where(binding.c.connection_id == row['id'])).mappings().all()
        original = SimpleNamespace(**row)
        if not settings['routing'].get('legacy_prefix'):
            for old_protocol in {row['protocol'], *(m['protocol'] for m in models)}:
                previous_base = api_base(original, old_protocol)
                if previous_base.startswith(address):
                    prefix = previous_base[len(address) :]
                    settings['routing']['prefixes'][canonical_protocol(old_protocol)] = prefix
                else:
                    raise RuntimeError('A legacy protocol address cannot be preserved without an explicit mapping')
        for model in models:
            override = canonical_protocol(model['protocol'])
            db.execute(
                binding.update()
                .where(binding.c.id == model['id'])
                .values(protocol_override=None if override == mode else override)
            )
        db.execute(
            connection.update()
            .where(connection.c.id == row['id'])
            .values(protocol=mode, base_url=address, settings=settings)
        )


def downgrade():
    raise RuntimeError('OI7 requires a compatible OI image; restore a pre-migration backup to downgrade')
