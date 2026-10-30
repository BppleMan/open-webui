"""Independent offline review probes at the post-remediation HEAD."""

import copy
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from open_webui.models.automations import AutomationRun
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import Chat, Chats, ChatForm
from open_webui.models.config import Config
from open_webui.models.customization import ChatDraft, ChatAnnotation, MessageRevision, Connection, ModelBinding
from open_webui.models.shared_chats import SharedChat
from open_webui.routers import customization, chats as chat_routes
from open_webui.utils import middleware
from open_webui.utils.oi import accounting, statistics, catalog, connection_migrations as migration
from open_webui.utils.oi.protocols import generate, NativeStream, build_request
from open_webui.utils.oi.temporary_native import restore
from test.oi.test_accounting import answer
from test.oi.test_connection_migrations import seed
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
async def test_save_as_copy_does_not_create_another_statistics_event(db):
    db.add(Chat(id='chat', user_id='u', title='Chat', chat={}, created_at=1, updated_at=1))
    await answer(db, 'chat', 'original', '0.03')
    original = {
        'role': 'assistant',
        'content': 'original',
        'model': 'model',
        'usage': {'input_tokens': 1, 'output_tokens': 1},
    }
    await ChatMessages.upsert_message('original', 'chat', 'u', original, db=db)
    before = await statistics.by(db)
    # Messages.svelte copies message.usage and adds oi_source_message_id.
    await ChatMessages.upsert_message(
        'copy',
        'chat',
        'u',
        {**original, 'content': 'edited copy', 'oi_source_message_id': 'original', 'oi_native_invalidated': True},
        db=db,
    )
    after = await statistics.by(db)
    assert after['model']['count'] == before['model']['count'], {'before': before, 'after': after}


@pytest.mark.asyncio
async def test_continue_legacy_reply_preserves_preexisting_usage(db):
    db.add(Chat(id='legacy', user_id='u', title='Legacy', chat={}, created_at=1, updated_at=1))
    await ChatMessages.upsert_message(
        'reply',
        'legacy',
        'u',
        {'role': 'assistant', 'content': 'old', 'model': 'model', 'usage': {'input_tokens': 100, 'output_tokens': 20}},
        db=db,
    )
    before = await statistics.by(db)
    await answer(db, 'legacy', 'reply', '0.01', request='continue-old')
    await ChatMessages.upsert_message(
        'reply', 'legacy', 'u', {'usage': {'input_tokens': 101, 'output_tokens': 21}}, db=db
    )
    after = await statistics.by(db)
    assert after['model']['input_tokens'] == 101, {'before': before, 'after': after}


@pytest.mark.asyncio
async def test_delete_all_owned_chats_erases_collaborators_content(db):
    conn = await db.connection()
    for table in (AutomationRun.__table__, SharedChat.__table__):
        await conn.run_sync(lambda sync, table=table: table.create(sync, checkfirst=True))
    db.add(Chat(id='shared-chat', user_id='owner', title='Shared', chat={}, created_at=1, updated_at=1))
    db.add(
        ChatDraft(
            user_id='collaborator',
            chat_id='shared-chat',
            payload={'prompt': 'unsent private text'},
            revision=1,
            updated_at=1,
        )
    )
    db.add(
        ChatAnnotation(
            user_id='collaborator',
            chat_id='shared-chat',
            data={'bookmarks': {'m': {'excerpt': 'private excerpt'}}},
            updated_at=1,
        )
    )
    db.add(
        MessageRevision(
            id='revision',
            user_id='collaborator',
            chat_id='shared-chat',
            message_id='m',
            content='old private content',
            output=[],
            created_at=1,
        )
    )
    await db.commit()
    assert await Chats.delete_chats_by_user_id('owner', db=db)
    assert await db.get(Chat, 'shared-chat') is None
    remaining = {
        table.__tablename__: len((await db.execute(select(table))).scalars().all())
        for table in (ChatDraft, ChatAnnotation, MessageRevision)
    }
    assert all(count == 0 for count in remaining.values()), remaining


@pytest.mark.asyncio
async def test_revoked_sharing_returns_404_even_though_chat_still_exists(db, monkeypatch):
    from open_webui.models import chats as chat_models

    conn = await db.connection()
    await conn.run_sync(lambda sync: SharedChat.__table__.create(sync, checkfirst=True))
    db.add(Chat(id='still-exists', user_id='owner', title='Shared', chat={}, created_at=1, updated_at=1))
    db.add(
        SharedChat(
            id='share',
            chat_id='still-exists',
            user_id='owner',
            title='Shared',
            chat={'share_mode': 'continue'},
            created_at=1,
            updated_at=1,
        )
    )
    await db.commit()
    user = SimpleNamespace(id='collaborator', role='user')
    monkeypatch.setattr(chat_models.AccessGrants, 'has_access', AsyncMock(return_value=True))
    assert (await customization.read_draft('still-exists', user, db))['revision'] == 0
    monkeypatch.setattr(chat_models.AccessGrants, 'has_access', AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as error:
        await customization.read_draft('still-exists', user, db)
    assert error.value.status_code == 404
    assert await db.get(Chat, 'still-exists') is not None


@pytest.mark.asyncio
async def test_openai_temporary_capsule_roundtrips_provider_specific_fields():
    details = [{'type': 'reasoning.encrypted', 'data': 'opaque-synthetic-state', 'index': 0}]
    native = {
        'choices': [
            {
                'message': {
                    'role': 'assistant',
                    'content': 'answer',
                    'provider_specific_fields': {'reasoning_details': details},
                },
                'finish_reason': 'stop',
            }
        ]
    }
    metadata = {'chat_id': 'temporary:transport', 'oi_temporary_id': 'stable', 'message_id': 'reply'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    result = await generate(
        request,
        {'metadata': metadata, 'messages': [{'role': 'user', 'content': 'hi'}]},
        SimpleNamespace(id='u'),
        binding('openai-chat'),
        connection('openai-chat'),
        'call',
        httpx.MockTransport(lambda _: httpx.Response(200, json=native)),
    )
    saved = {'id': 'reply', **result['choices'][0]['message'], 'oi_native_state': result['oi_native_state']}
    calls = restore(saved, 'u', 'stable')
    assert calls, saved


@pytest.mark.parametrize('stream', [False, True])
def test_native_gemini_image_output_reaches_canonical_response(stream):
    image = 'aW1hZ2UtcGF5bG9hZC1zeW50aGV0aWM='
    native = {
        'candidates': [
            {
                'content': {'role': 'model', 'parts': [{'inlineData': {'mimeType': 'image/png', 'data': image}}]},
                'finishReason': 'STOP',
            }
        ],
        'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 2},
    }
    from open_webui.utils.oi.capability_calls import image_outputs

    assert image_outputs(native) == ['data:image/png;base64,' + image]
    state = NativeStream('gemini')
    result = state.feed(native) if stream else state.nonstream(native)
    assert image in json.dumps(result), result


@pytest.mark.asyncio
async def test_rag_capability_error_is_not_reported_as_successful_zero_sources(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=AsyncMock(), RERANKING_FUNCTION=None))
    )
    events = []

    async def emitter(event):
        events.append(event)

    monkeypatch.setattr(
        middleware,
        'generate_queries',
        AsyncMock(return_value={'choices': [{'message': {'content': '{"queries":["literal query"]}'}}]}),
    )
    monkeypatch.setattr(middleware.Config, 'get_many', AsyncMock(return_value={}))
    monkeypatch.setattr(
        middleware,
        'get_sources_from_items',
        AsyncMock(
            side_effect=HTTPException(
                429, {'code': 'connection_capability_failed', 'capability': 'embedding', 'upstream_status': 429}
            )
        ),
    )
    body = {
        'model': 'oi:chat',
        'messages': [{'role': 'user', 'content': 'answer from my file'}],
        'metadata': {'files': [{'type': 'file', 'id': 'file'}]},
    }
    with pytest.raises(HTTPException) as error:
        await middleware.chat_completion_files_handler(
            request, body, {'__event_emitter__': emitter}, SimpleNamespace(id='u', role='admin')
        )
    assert error.value.status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['blocked', 'reasoning_only'])
async def test_nonstream_native_terminal_reply_always_finishes_ui(monkeypatch, case):
    native = (
        {'promptFeedback': {'blockReason': 'SAFETY'}, 'usageMetadata': {'promptTokenCount': 3}}
        if case == 'blocked'
        else {
            'candidates': [
                {
                    'content': {'role': 'model', 'parts': [{'text': 'brief thought', 'thought': True}]},
                    'finishReason': 'MAX_TOKENS',
                }
            ],
            'usageMetadata': {'promptTokenCount': 3, 'candidatesTokenCount': 0, 'thoughtsTokenCount': 4},
        }
    )
    response = NativeStream('gemini').nonstream(native)
    events = []

    async def emitter(event):
        events.append(event)

    monkeypatch.setattr(middleware, 'log_model_usage', lambda *a, **kw: None)
    monkeypatch.setattr(middleware, 'outlet_filter_handler', AsyncMock())
    monkeypatch.setattr(middleware, 'background_tasks_handler', AsyncMock())
    ctx = {
        'request': SimpleNamespace(state=SimpleNamespace()),
        'user': SimpleNamespace(id='u'),
        'metadata': {'chat_id': 'temporary:transport', 'message_id': 'reply'},
        'form_data': {'model': 'oi:leaf'},
        'events': [],
        'event_emitter': emitter,
    }
    await middleware.non_streaming_chat_response_handler(response, ctx)
    assert any(event.get('data', {}).get('done') for event in events), {'response': response, 'events': events}


@pytest.mark.asyncio
async def test_reuse_migration_accepts_matching_inherited_effective_protocol(db, monkeypatch):
    user, _ = await seed(db, monkeypatch)
    await Config.upsert(
        {
            'openai.enable': True,
            'openai.api_base_urls': ['https://source.invalid/v1'],
            'openai.api_keys': ['synthetic'],
            'openai.api_configs': {'0': {'model_ids': ['model']}},
        },
        db=db,
    )
    db.add(
        Connection(
            id='target',
            owner_id='u',
            name='Target',
            template='openai-compatible',
            protocol='openai-chat',
            transport='server',
            base_url='https://source.invalid',
            credentials='',
            settings={'routing': {'version': 1, 'profile': 'standard'}},
            shared=True,
            enabled=True,
            legacy_key='openai:0',
            revision=1,
            created_at=1,
            updated_at=1,
        )
    )
    # A model saved while inheriting Anthropic still has protocol='anthropic' after
    # the connection default is changed to OpenAI Chat; effective_protocol is Chat.
    db.add(
        ModelBinding(
            id='oi:m',
            connection_id='target',
            upstream_id='model',
            protocol='anthropic',
            protocol_override=None,
            name='Model',
            enabled=True,
            available=True,
            capabilities={'discovered': {'chat': True}},
            pricing={},
            params={},
            aliases=[],
            updated_at=1,
        )
    )
    await db.commit()
    preview = await migration.preview(db, user, 'server')
    result = await migration.apply(
        db,
        user,
        'server',
        preview['version'],
        [{'source_id': 'openai:0', 'action': 'reuse', 'connection_id': 'target', 'acknowledge_duplicate': False}],
    )
    assert 'model' in result['model_mapping']


@pytest.mark.asyncio
async def test_second_temporary_conversation_save_does_not_return_first_transcript(db, monkeypatch):
    # frontend_probes.mjs independently reproduces reuse of this ID by a new temporary draft.
    temporary = 'reused-temporary-draft'
    monkeypatch.setattr(Chats, 'require_chat_creation_permission', AsyncMock())
    monkeypatch.setattr(chat_routes, 'publish_event', AsyncMock())
    user = SimpleNamespace(id='u', role='admin', name='User')

    async def save(mid, text):
        return await chat_routes.create_new_chat(
            SimpleNamespace(),
            ChatForm(
                oi_temporary_id=temporary,
                chat={
                    'title': text,
                    'history': {
                        'currentId': mid,
                        'messages': {mid: {'id': mid, 'role': 'assistant', 'content': text, 'model': 'model'}},
                    },
                },
            ),
            user,
            db,
        )

    await answer(db, 'temporary:' + temporary, 'first', '0.03')
    first = await save('first', 'first conversation')
    await answer(db, 'temporary:' + temporary, 'second', '0.04')
    with pytest.raises(HTTPException) as error:
        await save('second', 'second conversation')
    assert error.value.status_code == 409
    saved = await Chats.get_chat_by_id(first.id, db=db)
    assert saved.chat['history']['currentId'] == 'first'


@pytest.mark.asyncio
async def test_stale_server_discovery_cannot_replace_new_connection_catalog(db, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    user = SimpleNamespace(id='u', role='admin')
    db.add(
        Connection(
            id='race',
            owner_id='u',
            name='Race',
            template='openai-compatible',
            protocol='openai-chat',
            transport='server',
            base_url='https://old.invalid',
            credentials='',
            settings={'routing': {'version': 1, 'profile': 'standard'}},
            shared=False,
            enabled=True,
            revision=1,
            created_at=1,
            updated_at=1,
        )
    )
    db.add(
        ModelBinding(
            id='oi:alpha',
            connection_id='race',
            upstream_id='alpha',
            protocol='openai-chat',
            name='Alpha',
            enabled=True,
            available=True,
            capabilities={'manual': False},
            pricing={},
            params={},
            aliases=[],
            updated_at=1,
        )
    )
    await db.commit()
    started, release = asyncio.Event(), asyncio.Event()

    async def old_provider(c, transport=None):
        assert c.base_url == 'https://old.invalid' and c.revision == 1
        started.set()
        await release.wait()
        return [{'id': 'alpha'}]

    monkeypatch.setattr(catalog, 'fetch_models', old_provider)
    discovery = asyncio.create_task(customization.discover_models('race', user, db))
    await started.wait()
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as other:
        current = await other.get(Connection, 'race')
        await customization.save_connection(
            other,
            user,
            customization.ConnectionForm(
                name='Race',
                template='openai-compatible',
                protocol='openai-chat',
                base_url='https://new.invalid',
                revision=1,
            ),
            current,
        )
        await catalog.apply_discovered_models(other, current, [{'id': 'beta'}])
        await other.commit()
    release.set()
    with pytest.raises(HTTPException) as error:
        await discovery
    assert error.value.status_code == 409
    rows = (await db.execute(select(ModelBinding).where(ModelBinding.connection_id == 'race'))).scalars().all()
    available = {row.upstream_id: row.available for row in rows}
    assert available == {'alpha': False, 'beta': True}, available
