import runpy
from pathlib import Path
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.migrations.versions.oi0000000001_add_customization_records import upgrade as oi1
from open_webui.migrations.versions.oi0000000005_connection_transport import upgrade as oi5
from open_webui.migrations.versions.oi0000000007_protocol_inheritance import upgrade as oi7


def test_populated_protocol_migration_preserves_routes_and_ids():
    fixture = runpy.run_path(str(Path(__file__).parents[2] / 'scripts/oi/protocol-migration-fixture.py'))
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            oi1()
            oi5()
            fixture['seed'](db)
            oi7()
        fixture['verify'](db)
        indexes = sa.inspect(db).get_indexes('oi_model_binding')
        assert any(i['name'] == 'ix_oi_binding_provider' and not i['unique'] for i in indexes)
    engine.dispose()
