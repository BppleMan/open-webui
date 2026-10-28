"""Opt-in synthetic PostgreSQL migration/restore acceptance on a loopback oi_* database.

Run prepare on an empty database, pg_dump/pg_restore to another database, then verify.
No real provider is called. See .github/workflows/oi.yml for the complete sequence.
"""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

repo = Path.cwd()
config = Config(str(repo / 'backend/open_webui/alembic.ini'))
config.set_main_option('script_location', str(repo / 'backend/open_webui/migrations'))
url = os.environ['DATABASE_URL']
target = sa.engine.make_url(url)
assert (
    target.host in {'127.0.0.1', 'localhost'} and target.username == 'oi_test' and target.database.startswith('oi_')
), 'Use an isolated loopback oi_test / oi_* database'
(repo / '.oi-local').mkdir(exist_ok=True)
mode = sys.argv[1]
assert mode in {'prepare', 'verify'}, 'Use prepare or verify'
if mode == 'prepare':
    guard = sa.create_engine(url)
    assert not sa.inspect(guard).get_table_names(), 'Prepare requires an empty database'
    guard.dispose()
    command.upgrade(config, '0bd252b206fb')
    engine = sa.create_engine(url)
    with engine.begin() as db:
        meta = sa.MetaData()
        chat = sa.Table('chat', meta, autoload_with=db)
        messages = sa.Table('chat_message', meta, autoload_with=db)
        message = {
            'id': 'm',
            'role': 'assistant',
            'model': 'legacy-model',
            'content': 'Historical synthetic reply',
            'output': [
                {
                    'type': 'message',
                    'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': 'Historical synthetic reply'}],
                }
            ],
            'oi_native': [
                {
                    'binding_id': 'oi:pg',
                    'protocol': 'anthropic',
                    'call_id': 'historic-call',
                    'body': {
                        'content': [
                            {'type': 'thinking', 'thinking': 'synthetic', 'signature': 'pg-signature'},
                            {'type': 'text', 'text': 'Historical synthetic reply'},
                        ]
                    },
                }
            ],
            'finish_reason': 'length',
            'done': True,
        }
        db.execute(
            chat.insert().values(
                id='pg-chat',
                user_id='pg-user',
                title='Synthetic migration history',
                chat={'history': {'currentId': 'm', 'messages': {'m': message}}},
                archived=False,
                pinned=False,
                meta={},
                created_at=1,
                updated_at=1,
            )
        )
        db.execute(
            messages.insert().values(
                id='pg-chat-m',
                chat_id='pg-chat',
                user_id='pg-user',
                role='assistant',
                content=message['content'],
                output=message['output'],
                model_id='legacy-model',
                done=True,
                created_at=1,
                updated_at=1,
            )
        )
    engine.dispose()
    command.upgrade(config, 'head')


async def run():

    from open_webui.internal.db import async_engine, get_async_db
    from open_webui.models.chat_messages import ChatMessages
    from open_webui.models.chats import Chats
    from open_webui.models.customization import Connection, Generation, ModelBinding
    from open_webui.utils.middleware import process_messages_with_output
    from open_webui.utils.oi import accounting, archive, catalog, drafts, runtime
    from open_webui.utils.oi.protocols import build_request

    async with get_async_db() as db:
        if mode == 'prepare':
            c = Connection(
                id='pg-connection',
                owner_id='pg-user',
                name='PG synthetic',
                template='anthropic',
                protocol='anthropic',
                base_url='https://synthetic.invalid/v1',
                credentials=catalog.encrypt_credentials({'api_key': 'synthetic-only'}),
                settings={},
                shared=False,
                enabled=True,
                revision=1,
                created_at=1,
                updated_at=1,
            )
            b = ModelBinding(
                id='oi:pg',
                connection_id=c.id,
                upstream_id='native-model',
                name='PG model',
                protocol='anthropic',
                enabled=True,
                available=True,
                capabilities={'output_limit': 4096},
                pricing={},
                params={},
                aliases=['legacy-model'],
                updated_at=1,
            )
            db.add_all([c, b])
            await db.flush()
            draft = await drafts.put_draft(db, 'pg-user', 'pg-chat', {'prompt': '中文草稿' * 1500}, 0, 'pg-draft-v1')
            assert len(draft['payload']['prompt']) == 6000
            for i, cost in enumerate(['0.03', '0.04', '0.05']):
                g, _ = await accounting.begin_generation(
                    db, 'pg-user', 'pg-chat', 'm', f'pg-request-{i}', 'regenerate' if i else 'assistant'
                )
                call, _ = await accounting.begin_call(
                    db, g, 'legacy-model', binding_id='oi:pg', connection_id=c.id, call_id=f'pg-call-{i}'
                )
                await accounting.settle_call(
                    db, call.id, {'input_tokens': 100, 'output_tokens': 20}, actual_usd=cost, source='synthetic'
                )
                await accounting.finish_generation(db, g.id)
            inherited = await accounting.inherit_snapshot(db, 'pg-user', 'pg-fork', 'pg-chat', 'm')
            assert inherited
            g, _ = await accounting.begin_generation(db, 'pg-user', 'pg-fork', 'fork-m', 'pg-request-fork')
            call, _ = await accounting.begin_call(db, g, 'legacy-model', call_id='pg-call-fork')
            await accounting.settle_call(
                db, call.id, {'input_tokens': 100, 'output_tokens': 20}, actual_usd='0.02', source='synthetic'
            )
            await accounting.finish_generation(db, g.id)
            await accounting.set_baseline(db, 'pg-user', 'pg-imported', 'batch', actual_usd='12.50')
            await accounting.inherit_history_baseline(db, 'pg-user', 'pg-import-fork', 'pg-imported', 'old-message')
            manifest = await archive.export_records(db, 'pg-user', 'pg-chat')
            await archive.restore_records(db, 'pg-user', manifest, 'pg-archive')
            await db.commit()
        assert (await accounting.get_totals(db, 'pg-chat'))['actual_usd'] == '0.12'
        assert (await accounting.get_totals(db, 'pg-fork'))['actual_usd'] == '0.14'
        assert (await accounting.get_totals(db, 'pg-import-fork'))['actual_usd'] == '12.50'
        assert (await accounting.get_totals(db, 'pg-archive'))['actual_usd'] == '0.12'
        draft = await drafts.get_draft(db, 'pg-user', 'pg-chat')
        assert len(draft['payload']['prompt']) == 6000 and draft['revision'] == 1
        c = await db.get(Connection, 'pg-connection')
        assert catalog.credentials(c)['api_key'] == 'synthetic-only'
        b = await db.get(ModelBinding, 'oi:pg')
        history = await Chats.get_chat_by_id('pg-chat', db=db)
        assert history.chat['history']['messages']['m']['content'] == 'Historical synthetic reply'
        message = (await ChatMessages.get_messages_map_by_chat_id('pg-chat', db=db))['m']
        assert message['finish_reason'] == 'length'
        _, body = build_request({'messages': process_messages_with_output([message])}, b, c)
        assert body['messages'][0]['content'][0]['signature'] == 'pg-signature'
    if mode == 'prepare':
        from types import SimpleNamespace

        from fastapi import HTTPException

        async def write_draft(index):
            async with get_async_db() as db:
                try:
                    await drafts.put_draft(db, 'pg-user', 'pg-race', {'prompt': str(index)}, 0, f'writer-{index}')
                    await db.commit()
                    return True
                except drafts.DraftConflict:
                    await db.rollback()
                    return False

        async def reserve():
            try:
                await runtime.reserve_generations(
                    SimpleNamespace(id='pg-user'),
                    {'chat_id': 'pg-race', 'oi_request_id': 'same-request'},
                    [{'model_id': 'oi:pg', 'message_id': 'same-message'}],
                )
                return True
            except HTTPException as error:
                assert error.status_code == 409
                return False

        assert sum(await asyncio.gather(*(write_draft(i) for i in range(6)))) == 1
        assert sum(await asyncio.gather(*(reserve() for _ in range(6)))) == 1
        async with get_async_db() as db:
            assert (
                len((await db.execute(sa.select(Generation).where(Generation.chat_id == 'pg-race'))).scalars().all())
                == 1
            )
        from open_webui.models.users import User
        from open_webui.models.customization import ConnectionMigration
        from open_webui.utils.oi import connection_migrations

        async with get_async_db() as db:
            db.add(
                User(
                    id='pg-browser-user',
                    email='pg-browser@example.invalid',
                    name='Synthetic browser',
                    role='user',
                    created_at=1,
                    updated_at=1,
                    last_active_at=1,
                    settings={
                        'ui': {
                            'directConnections': {
                                'OPENAI_API_BASE_URLS': ['https://a.invalid/v1', 'https://b.invalid/v1'],
                                'OPENAI_API_KEYS': ['synthetic-a', 'synthetic-b'],
                                'OPENAI_API_CONFIGS': {
                                    '0': {'model_ids': ['same-model']},
                                    '1': {'model_ids': ['same-model']},
                                },
                            }
                        }
                    },
                )
            )
            await db.commit()
            owner = SimpleNamespace(id='pg-browser-user', role='user')
            view = await connection_migrations.preview(db, owner, 'personal')
        choices = [
            {'source_id': entry['id'], 'action': 'create', 'connection_id': None, 'acknowledge_duplicate': False}
            for entry in view['entries']
        ]

        async def migrate():
            async with get_async_db() as db:
                result = await connection_migrations.apply(db, owner, 'personal', view['version'], choices)
                await db.commit()
                return result

        results = await asyncio.gather(*(migrate() for _ in range(6)))
        assert all(result == results[0] for result in results)
        async with get_async_db() as db:
            assert (
                len((await db.execute(sa.select(Connection).where(Connection.owner_id == owner.id))).scalars().all())
                == 2
            )
            assert (
                len(
                    (await db.execute(sa.select(ConnectionMigration).where(ConnectionMigration.scope == 'personal')))
                    .scalars()
                    .all()
                )
                == 1
            )
            assert 'OPENAI_API_KEYS' not in str((await db.get(User, owner.id)).settings)
        from unittest.mock import AsyncMock, patch
        from uuid import uuid4
        from open_webui.models.config import Config as AppConfig
        from open_webui.routers.customization import copy_browser_to_server, ServerCopyForm

        async with get_async_db() as db:
            await AppConfig.upsert({'direct.enable': True}, db=db)
            await db.commit()

        async def copy_connection():
            async with get_async_db() as db:
                return await copy_browser_to_server(
                    results[0]['connections'][0]['connection_id'],
                    ServerCopyForm(revision=1, request_id=uuid4()),
                    owner,
                    db,
                )

        with patch.object(catalog, 'fetch_models', AsyncMock(return_value=[{'id': 'same-model'}])):
            copies = await asyncio.gather(*(copy_connection() for _ in range(6)))
        assert all(value == copies[0] for value in copies)
        async with get_async_db() as db:
            assert (
                len((await db.execute(sa.select(Connection).where(Connection.owner_id == owner.id))).scalars().all())
                == 3
            )
            assert (
                len(
                    (await db.execute(sa.select(ConnectionMigration).where(ConnectionMigration.scope == 'server-copy')))
                    .scalars()
                    .all()
                )
                == 1
            )
    # Statistics use the same canonical projection on PostgreSQL and SQLite.
    from open_webui.utils.oi.statistics import by
    from open_webui.models.chat_messages import ChatMessages

    async with get_async_db() as db:
        totals = await by(db)
        daily = await ChatMessages.get_daily_message_counts_by_model(db=db)
        hourly = await ChatMessages.get_hourly_message_counts_by_model(db=db)
        assert sum(x['count'] for x in totals.values()) == sum(sum(x.values()) for x in daily.values())
        assert sum(x['count'] for x in totals.values()) == sum(sum(x.values()) for x in hourly.values())
    await async_engine.dispose()


asyncio.run(run())
engine = sa.create_engine(url)
with engine.connect() as db:
    tables = [
        'chat',
        'chat_message',
        'oi_connection',
        'oi_connection_migration',
        'user',
        'config',
        'oi_model_binding',
        'oi_chat_draft',
        'oi_chat_ledger',
        'oi_generation',
        'oi_model_call',
        'oi_cost_snapshot',
        'oi_import_cost_baseline',
        'oi_user_state_lock',
        'oi_import_receipt',
        'oi_archive_origin',
        'oi_chat_annotation',
        'oi_message_revision',
    ]
    manifest = {}
    for name in tables:
        table = sa.Table(name, sa.MetaData(), autoload_with=db)
        rows = [dict(row) for row in db.execute(sa.select(table)).mappings()]
        encoded = json.dumps(
            sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str)), sort_keys=True, default=str
        ).encode()
        manifest[name] = {'count': len(rows), 'sha256': hashlib.sha256(encoded).hexdigest()}
    revision = db.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one()
    assert revision == 'oi0000000006'
path = repo / '.oi-local/pg-before-restore.json'
if mode == 'prepare':
    path.write_text(json.dumps(manifest, indent=2))
    path.chmod(0o600)
else:
    assert manifest == json.loads(path.read_text()), 'Restored data differs'
print(
    json.dumps(
        {
            'mode': mode,
            'revision': revision,
            'parent_actual': '0.12',
            'fork_actual': '0.14',
            'draft_characters': 6000,
            'tables_verified': len(manifest),
            'native_replay': 'passed',
            'credential_roundtrip': 'passed',
            'concurrent_migration': 'one_migration_receipt_two_browser_connections_one_server_copy'
            if mode == 'prepare'
            else 'restored_manifest_matches',
            'concurrent_writers': 'single_winner' if mode == 'prepare' else 'restored_manifest_matches',
        }
    )
)
engine.dispose()
