import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from open_webui.models.config import Config
from open_webui.models.users import User
from open_webui.models.models import Models
from open_webui.models.customization import Connection, ModelBinding, ModelCall
from open_webui.routers import customization
from open_webui.utils.oi import catalog, connection_migrations as migration, browser_transport, protocols, runtime


async def seed(db, monkeypatch, duplicate=False):
    conn = await db.connection()
    await conn.run_sync(lambda sync: User.__table__.create(sync, checkfirst=True))
    await conn.run_sync(lambda sync: Config.__table__.create(sync, checkfirst=True))
    legacy = {
        'OPENAI_API_BASE_URLS': [
            'https://a.invalid/v1',
            'https://a.invalid/v1' if duplicate else 'https://b.invalid/v1',
        ],
        'OPENAI_API_KEYS': ['synthetic-key-a', 'synthetic-key-a' if duplicate else 'synthetic-key-b'],
        'OPENAI_API_CONFIGS': {
            '0': {'name': 'A', 'model_ids': ['same-model'], 'headers': {'X-Test': 'synthetic-header'}},
            '1': {
                'name': 'B',
                'model_ids': ['same-model'],
                'headers': {'X-Test': 'synthetic-header'} if duplicate else {},
            },
        },
    }
    db.add(
        User(
            id='u',
            email='u@example.invalid',
            name='Synthetic user',
            role='admin',
            settings={'ui': {'directConnections': legacy}},
            created_at=1,
            updated_at=1,
            last_active_at=1,
        )
    )
    await db.commit()
    monkeypatch.setattr(Models, 'get_model_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=True))
    monkeypatch.setattr(Config, 'DEFAULTS', {})
    return SimpleNamespace(id='u', role='admin'), legacy


def choices(preview, ack=False):
    return [
        {'source_id': item['id'], 'action': 'create', 'connection_id': None, 'acknowledge_duplicate': ack}
        for item in preview['entries']
        if not item['issue']
    ]


@pytest.mark.asyncio
async def test_personal_migration_separates_duplicate_model_names_encrypts_and_is_idempotent(db, monkeypatch):
    user, legacy = await seed(db, monkeypatch)
    view = await migration.preview(db, user, 'personal')
    assert 'synthetic-key' not in json.dumps(view) and 'synthetic-header' not in json.dumps(view)
    result = await migration.apply(db, user, 'personal', view['version'], choices(view))
    await db.commit()
    connections = (await db.execute(select(Connection))).scalars().all()
    bindings = (await db.execute(select(ModelBinding))).scalars().all()
    assert len(connections) == len(bindings) == 2
    assert len(set(result['model_mapping']['same-model'])) == 2
    assert all(c.transport == 'browser' and not c.shared and 'synthetic-key' not in c.credentials for c in connections)
    assert {catalog.credentials(c)['api_key'] for c in connections} == set(legacy['OPENAI_API_KEYS'])
    assert 'synthetic' not in json.dumps((await db.get(User, 'u')).settings)
    assert await migration.apply(db, user, 'personal', view['version'], choices(view)) == result
    assert len((await db.execute(select(Connection))).scalars().all()) == 2
    # Changing display order/names cannot change IDs, credentials or upstream model routing.
    connections[0].name = 'Renamed'
    await db.commit()
    assert [b.id for b in bindings] == [b.id for b in (await db.execute(select(ModelBinding))).scalars()]
    assert {b.upstream_id for b in bindings} == {'same-model'}


@pytest.mark.asyncio
async def test_stale_preview_and_duplicate_sources_are_explicit_conflicts(db, monkeypatch):
    user, legacy = await seed(db, monkeypatch, duplicate=True)
    view = await migration.preview(db, user, 'personal')
    assert all(item['ambiguous'] for item in view['entries'])
    with pytest.raises(HTTPException) as error:
        await migration.apply(db, user, 'personal', view['version'], choices(view))
    assert error.value.status_code == 409
    await db.rollback()
    assert not (await db.execute(select(Connection))).scalars().all()
    row = await db.get(User, 'u')
    changed = copy.deepcopy(row.settings)
    changed['ui']['directConnections']['OPENAI_API_KEYS'][1] = 'new-synthetic-key'
    row.settings = changed
    await db.commit()
    with pytest.raises(HTTPException) as stale:
        await migration.apply(db, user, 'personal', view['version'], choices(view, True))
    assert stale.value.status_code == 409
    await db.rollback()
    assert not (await db.execute(select(Connection))).scalars().all()
    assert 'OPENAI_API_KEYS' in (await db.get(User, 'u')).settings['ui']['directConnections']


@pytest.mark.asyncio
async def test_browser_config_owner_only_no_store_and_soft_delete_preserves_other_binding(db, monkeypatch):
    user, _ = await seed(db, monkeypatch)
    view = await migration.preview(db, user, 'personal')
    result = await migration.apply(db, user, 'personal', view['version'], choices(view))
    await db.commit()
    a, b = result['connections']
    cid = a['connection_id']
    response = await customization.browser_config(cid, user=user, db=db)
    assert 'no-store' in response.headers['cache-control']
    assert json.loads(response.body)['headers']['Authorization'] == 'Bearer synthetic-key-a'
    for role in ['user', 'admin']:
        other = SimpleNamespace(id='other', role=role)
        with pytest.raises(HTTPException) as denied:
            await customization.browser_config(cid, user=other, db=db)
        assert denied.value.status_code == 404
        assert not await catalog.model_catalog(db, other)
    await customization.set_connection_removed(db, user, cid, 1, True)
    with pytest.raises(HTTPException):
        await customization.browser_config(cid, user=user, db=db)
    assert {model['id'] for model in await catalog.model_catalog(db, user)} == set(b['binding_ids'])
    await customization.set_connection_removed(db, user, cid, 2, False)
    assert (await db.get(Connection, cid)).enabled is False
    assert (await db.get(ModelBinding, a['binding_ids'][0])).upstream_id == 'same-model'
    assert (await db.get(ModelBinding, b['binding_ids'][0])).connection_id == b['connection_id']


@pytest.mark.asyncio
async def test_auxiliary_migration_is_atomic_and_preserves_native_options(db, monkeypatch):
    user, _ = await seed(db, monkeypatch)
    initial = {
        'rag.embedding_engine': 'openai',
        'rag.embedding_model': 'embed',
        'rag.openai.api_base_url': 'https://embed.invalid/v1',
        'rag.openai.api_key': 'synthetic-embed',
        'rag.reranking_engine': 'external',
        'rag.reranking_model': 'rank',
        'rag.external_reranker_url': 'https://rank.invalid/v1/rerank',
        'rag.external_reranker_api_key': 'synthetic-rank',
        'audio.tts.engine': 'openai',
        'audio.tts.model': 'voice',
        'audio.tts.openai.api_base_url': 'https://voice.invalid/v1',
        'audio.tts.openai.api_key': 'synthetic-voice',
        'audio.tts.openai.params': {'speed': 1.2},
    }
    await Config.upsert(initial, db=db)
    await db.commit()
    view = await migration.preview(db, user, 'server')
    result = await migration.apply(db, user, 'server', view['version'], choices(view))
    await db.commit()
    assert len(result['connections']) == 3
    rows = {row.key: row.value for row in (await db.execute(select(Config))).scalars()}
    assert rows['rag.embedding_engine'] == rows['rag.reranking_engine'] == rows['audio.tts.engine'] == 'connection'
    voice = await db.get(ModelBinding, rows['audio.tts.model'])
    assert voice.params['capability_params']['tts']['speed'] == 1.2
    rank = await db.get(ModelBinding, rows['rag.reranking_model'])
    assert (await db.get(Connection, rank.connection_id)).settings['rerank_path'] == '/rerank'


@pytest.mark.asyncio
async def test_server_copy_failure_preserves_source_and_retry_reuses_mapping(db, monkeypatch):
    from uuid import uuid4

    user, _ = await seed(db, monkeypatch)
    view = await migration.preview(db, user, 'personal')
    result = await migration.apply(db, user, 'personal', view['version'], choices(view))
    await db.commit()
    cid = result['connections'][0]['connection_id']
    form = customization.ServerCopyForm(revision=1, request_id=uuid4())
    monkeypatch.setattr(catalog, 'fetch_models', AsyncMock(side_effect=ValueError('synthetic offline')))
    with pytest.raises(HTTPException):
        await customization.copy_browser_to_server(cid, form, user, db)
    await db.rollback()
    assert len((await db.execute(select(Connection))).scalars().all()) == 2
    monkeypatch.setattr(catalog, 'fetch_models', AsyncMock(return_value=[{'id': 'same-model'}]))
    copied = await customization.copy_browser_to_server(cid, form, user, db)
    repeated = await customization.copy_browser_to_server(
        cid, customization.ServerCopyForm(revision=1, request_id=uuid4()), user, db
    )
    assert copied == repeated and len((await db.execute(select(Connection))).scalars().all()) == 3
    assert (await db.get(Connection, cid)).transport == 'browser'
    assert (await db.get(Connection, copied['connection_id'])).transport == 'server'


@pytest.mark.asyncio
async def test_browser_disconnect_retains_partial_usage_and_rejects_background(db, monkeypatch):
    from open_webui.socket import main as socket

    user, _ = await seed(db, monkeypatch)
    view = await migration.preview(db, user, 'personal')
    result = await migration.apply(db, user, 'personal', view['version'], choices(view))
    await db.commit()
    binding = await db.get(ModelBinding, result['connections'][0]['binding_ids'][0])
    connection = await db.get(Connection, binding.connection_id)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    monkeypatch.setattr(socket, 'SESSION_POOL', {'sid': {'id': 'u'}})
    monkeypatch.setattr(socket.sio, 'emit', AsyncMock())

    async def relay(event):
        data = event['data']
        assert data['model']['connection']['id'] == connection.id
        assert data['form_data']['model'] == 'same-model'
        queue = socket.EVENT_QUEUES[data['channel']]
        for part in ['data: {"choices":[],"usage":{"prompt_', 'tokens":123}}\n\n']:
            await queue.put(part)
        return {'status_code': 200, 'headers': {'content-type': 'text/event-stream'}}

    monkeypatch.setattr(socket, 'get_event_call', AsyncMock(return_value=relay))
    metadata = {'session_id': 'sid', 'user_id': 'u', 'oi_temporary_id': 'browser-test'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    form = {
        'model': binding.id,
        'messages': [{'role': 'user', 'content': 'synthetic'}],
        'stream': True,
        'metadata': metadata,
    }
    transport = browser_transport.BrowserTransport(user, metadata, binding, connection)
    response = await runtime.track_call(
        request,
        form,
        user,
        lambda call_id: protocols.generate(request, form, user, binding, connection, call_id, transport),
        binding,
        connection,
    )
    iterator = response.body_iterator.__aiter__()
    first = await anext(iterator)
    assert '123' in first
    socket.SESSION_POOL.clear()
    with pytest.raises(HTTPException):
        await anext(iterator)
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert call.usage['input_tokens'] == 123 and call.status == 'failed'
    assert call.usage['context']['transport'] == 'browser' and call.estimated_usd is None
    assert not socket.EVENT_QUEUES
    with pytest.raises(HTTPException) as denied:
        browser_transport.require_live_session({}, user)
    assert denied.value.status_code == 422


@pytest.mark.asyncio
async def test_late_migration_failure_rolls_back_connections_and_plaintext_cleanup(db, monkeypatch):
    user, _ = await seed(db, monkeypatch)
    await Config.upsert(
        {
            'openai.api_base_urls': ['https://legacy.invalid/v1'],
            'openai.api_keys': ['synthetic-old'],
            'openai.api_configs': {'0': {'model_ids': ['same-model']}},
        },
        db=db,
    )
    await db.commit()
    view = await migration.preview(db, user, 'server')
    original = Config.upsert

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('synthetic late write failure')

    monkeypatch.setattr(Config, 'upsert', fail)
    with pytest.raises(RuntimeError):
        await customization.migration_apply(
            customization.MigrationApplyForm(scope='server', version=view['version'], choices=choices(view)),
            SimpleNamespace(),
            user,
            db,
        )
    assert not (await db.execute(select(Connection))).scalars().all()
    assert (await db.get(Config, 'openai.api_keys')).value == ['synthetic-old']


@pytest.mark.asyncio
async def test_realtime_migration_preserves_both_models_and_cleans_credentials(db, monkeypatch):
    user, _ = await seed(db, monkeypatch)
    await Config.upsert(
        {
            'audio.realtime.enabled': True,
            'audio.realtime.provider': 'openai',
            'audio.realtime.openai.api_base_url': 'https://voice.invalid/v1',
            'audio.realtime.openai.api_key': 'synthetic-voice',
            'audio.realtime.model': 'realtime',
            'audio.realtime.transcription_model': 'transcribe',
            'audio.realtime.input_params': {'turn_detection': {'type': 'server_vad'}},
            'audio.realtime.voice': 'marin',
        },
        db=db,
    )
    await db.commit()
    view = await migration.preview(db, user, 'server')
    await migration.apply(db, user, 'server', view['version'], choices(view))
    await db.commit()
    config = {row.key: row.value for row in (await db.execute(select(Config))).scalars()}
    assert config['audio.realtime.provider'] == 'connection' and config['audio.realtime.openai.api_key'] == ''
    assert (
        config['audio.realtime.voice'] == 'marin'
        and config['audio.realtime.input_params']['turn_detection']['type'] == 'server_vad'
    )
    assert catalog.capabilities(await db.get(ModelBinding, config['audio.realtime.model'])) == {'realtime': True}
    assert catalog.capabilities(await db.get(ModelBinding, config['audio.realtime.transcription_model'])) == {
        'stt': True
    }


@pytest.mark.asyncio
async def test_duplicate_discovery_and_explicit_reuse_preserve_user_overrides(db, monkeypatch):
    user, legacy = await seed(db, monkeypatch)
    row = await db.get(User, user.id)
    legacy['OPENAI_API_CONFIGS']['0']['model_ids'] = ['same-model', 'same-model']
    legacy['OPENAI_API_CONFIGS']['0']['extra_params'] = {'temperature': 0.3, 'native_extension': True}
    row.settings = {'ui': {'directConnections': legacy}}
    await db.commit()
    view = await migration.preview(db, user, 'personal')
    first = await migration.apply(db, user, 'personal', view['version'], choices(view)[:1])
    await db.commit()
    cid = first['connections'][0]['connection_id']
    binding = (await db.execute(select(ModelBinding).where(ModelBinding.connection_id == cid))).scalar_one()
    assert binding.params['provider_options']['native_extension'] is True
    binding.params = {'temperature': 0.7}
    connection = await db.get(Connection, cid)
    connection.name = 'User override'
    await db.commit()
    view = await migration.preview(db, user, 'personal')
    choice = [{'source_id': 'openai:1', 'action': 'reuse', 'connection_id': cid, 'acknowledge_duplicate': False}]
    result = await migration.apply(db, user, 'personal', view['version'], choice)
    await db.commit()
    assert (await db.get(Connection, cid)).name == 'User override' and binding.params == {'temperature': 0.7}
    assert len((await db.execute(select(Connection))).scalars().all()) == 1
    with pytest.raises(HTTPException) as conflict:
        await migration.apply(db, user, 'personal', view['version'], choices(view))
    assert conflict.value.status_code == 409


def test_browser_alias_cannot_replace_existing_legacy_server_model():
    from open_webui.utils.oi.model_targets import remap_legacy_reference

    aliases = {'same-model': ['oi:browser-a']}
    with pytest.raises(HTTPException) as error:
        remap_legacy_reference('same-model', aliases, {'same-model': {'id': 'same-model'}})
    assert error.value.status_code == 409
    assert (
        remap_legacy_reference(
            'preset',
            {'preset': ['oi:browser-a']},
            {'preset': {'id': 'preset', 'preset': True, 'info': {'base_model_id': 'legacy'}}},
        )
        == 'preset'
    )
    assert remap_legacy_reference('oi:browser-a', aliases, {}) == 'oi:browser-a'
