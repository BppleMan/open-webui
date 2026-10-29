import os
import tempfile

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='oi-test-data-')
os.environ['DATABASE_URL'] = f'sqlite:///{os.environ["DATA_DIR"]}/application.db'
os.environ['ENABLE_DB_MIGRATIONS'] = 'false'
os.environ['USE_SLIM_DOCKER'] = 'true'
os.environ['WEBUI_SECRET_KEY'] = 'oi-synthetic-test-key-not-for-production'

import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.migrations.versions.oi0000000001_add_customization_records import upgrade
from open_webui.migrations.versions.oi0000000002_snapshot_totals import upgrade as upgrade_snapshots
from open_webui.migrations.versions.oi0000000004_baseline_anchors import upgrade as upgrade_anchors
from open_webui.migrations.versions.oi0000000005_connection_transport import upgrade as upgrade_transport
from open_webui.migrations.versions.oi0000000006_import_integrity import upgrade as upgrade_imports
from open_webui.migrations.versions.oi0000000007_protocol_inheritance import upgrade as upgrade_protocols
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db(monkeypatch):
    monkeypatch.setattr('open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:

        def migrate(connection):
            # Current upstream message schema; migration 3 has a separate legacy-schema test.
            Chat.__table__.create(connection)
            ChatMessage.__table__.create(connection)
            with Operations.context(MigrationContext.configure(connection)):
                upgrade()
                upgrade_snapshots()
                upgrade_anchors()
                upgrade_transport()
                upgrade_imports()
                upgrade_protocols()

        await conn.run_sync(migrate)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
