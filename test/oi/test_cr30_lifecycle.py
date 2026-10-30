"""Offline regression tests for the full-history review. No provider calls."""

import copy
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from open_webui.models.automations import AutomationRun
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import Chat, Chats, ChatForm
from open_webui.models.config import Config
from open_webui.models.models import Models
from open_webui.models.customization import Connection, ModelBinding, ChatDraft
from open_webui.models.shared_chats import SharedChat
from open_webui.routers import chats as chat_routes, customization, openai
from open_webui.utils import embeddings
from open_webui.utils.oi import accounting, catalog, connection_migrations as migration, drafts
from open_webui.utils.oi.protocols import build_request
from open_webui.utils.payload import apply_model_params_to_body_openai
from open_webui.utils import models as model_utils
from test.oi.test_accounting import answer
from test.oi.test_connection_migrations import seed
from test.oi.test_review_regressions import binding, connection


@pytest.mark.parametrize('protocol', ['openai-chat', 'openai-responses', 'anthropic', 'gemini'])
def test_capability_specific_configuration_is_not_sent_as_chat_parameter(protocol):
    params = {'capability_params': {'tts': {'speed': 1.2}}, 'temperature': 0.7}
    form = apply_model_params_to_body_openai(
        copy.deepcopy(params), {'model': 'oi:leaf', 'messages': [{'role': 'user', 'content': 'hello'}]}
    )
    _, body = build_request(form, binding(protocol), connection(protocol))
    assert 'capability_params' not in body, body


@pytest.mark.asyncio
async def test_deleting_chat_removes_retrievable_draft_content(db):
    conn = await db.connection()
    for table in (AutomationRun.__table__, SharedChat.__table__):
        await conn.run_sync(lambda sync, table=table: table.create(sync, checkfirst=True))
    db.add(Chat(id='erase-me', user_id='u', title='Erase me', chat={}, created_at=1, updated_at=1))
    await drafts.put_draft(db, 'u', 'erase-me', {'prompt': 'private text scheduled for deletion'}, 0, 'draft-1')
    await db.commit()
    assert await Chats.delete_chat_by_id_and_user_id('erase-me', 'u', db=db)
    assert await db.get(Chat, 'erase-me') is None
    remaining = await customization.draft_list(SimpleNamespace(id='u'), db)
    assert remaining == [], remaining


@pytest.mark.asyncio
async def test_reusing_phase_one_connection_does_not_discard_new_legacy_models(db, monkeypatch):
    user, _ = await seed(db, monkeypatch)
    await Config.upsert(
        {
            'openai.enable': True,
            'openai.api_base_urls': ['https://source.invalid/v1'],
            'openai.api_keys': ['synthetic-source'],
            'openai.api_configs': {'0': {'model_ids': ['old-model', 'new-model']}},
        },
        db=db,
    )
    db.add(
        Connection(
            id='phase-one',
            owner_id='u',
            name='Existing source',
            template='openai-compatible',
            protocol='openai-chat',
            transport='server',
            base_url='https://source.invalid',
            credentials=catalog.encrypt_credentials({'api_key': 'synthetic-source'}),
            settings={},
            shared=True,
            enabled=True,
            legacy_key='openai:0',
            revision=1,
            created_at=1,
            updated_at=1,
        )
    )
    db.add(
        ModelBinding(
            id='oi:old',
            connection_id='phase-one',
            upstream_id='old-model',
            protocol='openai-chat',
            name='Old',
            enabled=True,
            available=True,
            capabilities={'discovered': {'chat': True}},
            pricing={},
            params={},
            aliases=['old-model'],
            updated_at=1,
        )
    )
    await db.commit()
    preview = await migration.preview(db, user, 'server')
    entry = next(e for e in preview['entries'] if e['id'] == 'openai:0')
    assert entry['existing_connection']['id'] == 'phase-one'
    result = await migration.apply(
        db,
        user,
        'server',
        preview['version'],
        [{'source_id': 'openai:0', 'action': 'reuse', 'connection_id': 'phase-one', 'acknowledge_duplicate': False}],
    )
    await db.commit()
    state = await migration.snapshot(db, user, 'server')
    assert state['openai.api_configs']['0']['enable'] is False
    assert state['openai.api_keys'] == ['']
    assert 'new-model' in result['model_mapping'], result


@pytest.mark.asyncio
async def test_save_temporary_chat_preserves_existing_billing_identity(db, monkeypatch):
    # Saving supplies the stable temporary identity; calls are inherited, not copied.
    await answer(db, 'temporary:browser-id', 'a1', '0.03')
    messages = {
        'u1': {'id': 'u1', 'role': 'user', 'content': 'hello', 'parentId': None, 'childrenIds': ['a1']},
        'a1': {
            'id': 'a1',
            'role': 'assistant',
            'content': 'answer',
            'parentId': 'u1',
            'childrenIds': [],
            'model': 'oi:leaf',
        },
    }
    form = ChatForm(
        oi_temporary_id='browser-id',
        chat={
            'title': 'Saved temporary chat',
            'models': ['oi:leaf'],
            'params': {},
            'history': {'messages': messages, 'currentId': 'a1'},
            'messages': list(messages.values()),
            'timestamp': 1,
        },
    )
    monkeypatch.setattr(Chats, 'require_chat_creation_permission', AsyncMock())
    monkeypatch.setattr(chat_routes, 'publish_event', AsyncMock())
    result = await chat_routes.create_new_chat(
        SimpleNamespace(), form, SimpleNamespace(id='u', role='admin', name='User'), db
    )
    total = await accounting.get_totals(db, result.id)
    assert Decimal(total['actual_usd']) == Decimal('0.03'), total


@pytest.mark.asyncio
@pytest.mark.parametrize('registered', [False, True])
async def test_renaming_migrated_binding_preserves_original_access_policy(db, monkeypatch, registered):
    db.add(
        Connection(
            id='shared',
            owner_id='admin',
            name='Shared',
            template='openai-compatible',
            protocol='openai-chat',
            transport='server',
            base_url='https://source.invalid',
            credentials='',
            settings={},
            shared=True,
            enabled=True,
            revision=1,
            created_at=1,
            updated_at=1,
        )
    )
    db.add(
        ModelBinding(
            id='oi:restricted',
            connection_id='shared',
            upstream_id='restricted',
            protocol='openai-chat',
            name='Restricted',
            enabled=True,
            available=True,
            capabilities={
                'discovered': {'chat': True},
                'overrides': {},
                'legacy_model_id': 'restricted',
                'legacy_unregistered': not registered,
            },
            pricing={},
            params={},
            aliases=['restricted'],
            updated_at=1,
        )
    )
    await db.commit()
    legacy = SimpleNamespace(id='restricted', user_id='admin', base_model_id=None)

    async def get_model(mid, **kwargs):
        return legacy if registered and mid == 'restricted' else None

    monkeypatch.setattr(Models, 'get_model_by_id', get_model)
    monkeypatch.setattr(model_utils.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(model_utils.AccessGrants, 'has_access', AsyncMock(return_value=False))
    ordinary = SimpleNamespace(id='ordinary', role='user')
    with pytest.raises(Exception):
        await model_utils.check_model_access(ordinary, {'id': 'oi:restricted', 'oi_binding': True}, db=db)
    # Same editable values as ConnectionManager.saveModel; only the display name changes.
    await customization.save_binding(
        db,
        SimpleNamespace(id='admin', role='admin'),
        customization.BindingForm(
            connection_id='shared',
            upstream_id='restricted',
            name='Renamed',
            enabled=True,
            capability_overrides={},
            params={},
            pricing={},
        ),
        'oi:restricted',
    )
    with pytest.raises(Exception):
        await model_utils.check_model_access(ordinary, {'id': 'oi:restricted', 'oi_binding': True}, db=db)


@pytest.mark.asyncio
async def test_public_embeddings_dispatches_binding_and_never_legacy_zero(db, monkeypatch):
    from contextlib import asynccontextmanager
    import httpx
    from open_webui.internal import db as db_module
    from open_webui.utils.oi import capability_calls
    from open_webui.models.customization import ModelCall
    from test.oi.test_capability_calls import fixtures

    user = await fixtures(db, monkeypatch)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(db_module, 'get_async_db', session)
    observed = []

    async def provider(request):
        observed.append((str(request.url), json.loads(request.content)))
        return httpx.Response(
            200, json={'data': [{'index': 0, 'embedding': [0.1, 0.2]}], 'usage': {'prompt_tokens': 3}}
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        capability_calls.httpx, 'AsyncClient', lambda **kw: client(**{**kw, 'transport': httpx.MockTransport(provider)})
    )
    monkeypatch.setattr(embeddings, 'openai_embeddings', AsyncMock(side_effect=AssertionError('Legacy fallback')))
    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={})))
    result = await embeddings.generate_embeddings(request, {'model': 'oi:b', 'input': ['synthetic text']}, user)
    assert result['data'][0]['embedding'] == [0.1, 0.2]
    assert 'b.invalid' in observed[0][0]
    assert observed[0][1]['model'] == 'same-model'
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert call.binding_id == 'oi:b' and call.usage['input_tokens'] == 3


def test_temporary_capsule_is_scoped_and_edit_safe():
    from fastapi import HTTPException
    from open_webui.utils.oi.temporary_native import seal, restore

    native = [{'call_id': 'c', 'binding_id': 'oi:a', 'protocol': 'gemini', 'body': {'signature': 'synthetic'}}]
    output = [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'answer'}]}]
    capsule = seal(native, output, 'owner', 'temp', 'm')
    for uid, tid, mid in [('other', 'temp', 'm'), ('owner', 'other', 'm'), ('owner', 'temp', 'other')]:
        with pytest.raises(HTTPException):
            restore({'id': mid, 'output': output, 'oi_native_state': capsule}, uid, tid)
    message = {'id': 'm', 'output': output, 'oi_native_state': capsule}
    assert restore(message, 'owner', 'temp') == native
    edited = {'id': 'm', 'output': [], 'oi_native_state': capsule}
    assert restore(edited, 'owner', 'temp') == [] and edited['oi_native_invalidated']


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['single', 'owner', 'folder', 'all'])
async def test_every_delete_path_clears_content_but_retains_accounting(db, method):
    from open_webui.models.customization import MessageRevision, ChatAnnotation, ModelCall
    from sqlalchemy import func

    conn = await db.connection()
    for table in (AutomationRun.__table__, SharedChat.__table__):
        await conn.run_sync(lambda sync, table=table: table.create(sync, checkfirst=True))
    for cid in ['delete', 'keep']:
        db.add(
            Chat(
                id=cid,
                user_id='u' if cid == 'delete' else 'other',
                folder_id='folder',
                title=cid,
                chat={},
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            ChatDraft(
                user_id='u' if cid == 'delete' else 'other',
                chat_id=cid,
                revision=1,
                payload={'prompt': 'synthetic private'},
                updated_at=1,
            )
        )
        db.add(
            MessageRevision(
                id='revision-' + cid,
                user_id='u' if cid == 'delete' else 'other',
                chat_id=cid,
                message_id='m',
                content='old',
                output=[],
                created_at=1,
            )
        )
        db.add(
            ChatAnnotation(
                user_id='u' if cid == 'delete' else 'other', chat_id=cid, data={'text': 'private'}, updated_at=1
            )
        )
    await answer(db, 'delete', 'm', '0.03')
    await db.commit()
    methods = {
        'single': lambda: Chats.delete_chat_by_id('delete', db),
        'owner': lambda: Chats.delete_chat_by_id_and_user_id('delete', 'u', db),
        'folder': lambda: Chats.delete_chats_by_user_id_and_folder_id('u', 'folder', db),
        'all': lambda: Chats.delete_chats_by_user_id('u', db),
    }
    assert not await Chats.delete_chat_by_id_and_user_id('keep', 'u', db)
    assert await methods[method]()
    for table in (ChatDraft, MessageRevision, ChatAnnotation):
        assert await db.scalar(select(func.count()).select_from(table).where(table.chat_id == 'delete')) == 0
        assert await db.scalar(select(func.count()).select_from(table).where(table.chat_id == 'keep')) == 1
    assert await db.scalar(select(func.count()).select_from(ModelCall)) == 1


@pytest.mark.asyncio
async def test_temporary_save_is_owned_idempotent_and_atomic(db, monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy import func
    from open_webui.models.customization import ModelCall, ChatLedger

    await answer(db, 'temporary:save-temp', 'm', '0.03')
    await db.commit()
    monkeypatch.setattr(Chats, 'require_chat_creation_permission', AsyncMock())
    monkeypatch.setattr(chat_routes, 'publish_event', AsyncMock())
    form = ChatForm(
        oi_temporary_id='save-temp',
        chat={'history': {'messages': {'m': {'id': 'm', 'role': 'assistant', 'content': 'answer'}}, 'currentId': 'm'}},
    )
    with pytest.raises(HTTPException) as error:
        await chat_routes.create_new_chat(
            SimpleNamespace(), form.model_copy(deep=True), SimpleNamespace(id='other', role='admin', name='Other'), db
        )
    assert error.value.status_code == 403
    real_insert = ChatMessages.upsert_message
    monkeypatch.setattr(
        ChatMessages, 'upsert_message', AsyncMock(side_effect=RuntimeError('synthetic insertion failure'))
    )
    with pytest.raises(HTTPException):
        await chat_routes.create_new_chat(
            SimpleNamespace(), form.model_copy(deep=True), SimpleNamespace(id='u', role='admin', name='Owner'), db
        )
    assert await db.scalar(select(func.count()).select_from(Chat)) == 0
    assert await db.scalar(select(func.count()).select_from(ChatLedger)) == 1
    monkeypatch.setattr(ChatMessages, 'upsert_message', real_insert)
    saved = await chat_routes.create_new_chat(
        SimpleNamespace(), form.model_copy(deep=True), SimpleNamespace(id='u', role='admin', name='Owner'), db
    )
    retry = await chat_routes.create_new_chat(
        SimpleNamespace(), form.model_copy(deep=True), SimpleNamespace(id='u', role='admin', name='Owner'), db
    )
    assert saved.id == retry.id
    assert await db.scalar(select(func.count()).select_from(ModelCall)) == 1
    assert (await accounting.get_totals(db, saved.id))['actual_usd'] == '0.03'


@pytest.mark.asyncio
async def test_atomic_chat_insert_uses_explicit_session_when_sharing_disabled(db, monkeypatch):
    from open_webui.models.chat_messages import ChatMessage
    from sqlalchemy import func

    monkeypatch.setattr('open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING', False)
    monkeypatch.setattr(Chats, 'require_chat_creation_permission', AsyncMock())
    await Chats.insert_new_chat(
        'atomic',
        'u',
        ChatForm(
            chat={
                'history': {
                    'currentId': 'm',
                    'messages': {'m': {'id': 'm', 'role': 'assistant', 'content': 'synthetic'}},
                }
            }
        ),
        db=db,
        commit=False,
    )
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 1
    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(Chat)) == 0
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 0
