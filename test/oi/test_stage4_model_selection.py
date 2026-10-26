from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from open_webui.utils.task import get_task_model_id


@pytest.mark.parametrize('connection_type', ['local', 'external'])
def test_explicit_missing_task_binding_never_falls_back_to_conversation(connection_type):
    pool = {'oi:chat-a': {'id': 'oi:chat-a', 'connection_type': connection_type}}
    with pytest.raises(HTTPException) as error:
        get_task_model_id('oi:chat-a', 'oi:missing-b', 'oi:missing-b', pool)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    'target',
    [{'direct': True}, {'capabilities': {'embedding': True}}, {'info': {'meta': {'capabilities': {'chat': False}}}}],
)
def test_explicit_task_model_requires_server_chat_capability(target):
    pool = {'oi:a': {'id': 'oi:a'}, 'oi:b': {'id': 'oi:b', **target}}
    with pytest.raises(HTTPException) as error:
        get_task_model_id('oi:a', '', 'oi:b', pool)
    assert error.value.status_code == 422


def test_task_default_and_explicit_same_name_binding_are_distinct():
    pool = {'oi:a': {'id': 'oi:a', 'name': 'same-model'}, 'oi:b': {'id': 'oi:b', 'name': 'same-model'}}
    assert get_task_model_id('oi:a', '', '', pool) == 'oi:a'
    assert get_task_model_id('oi:a', '', 'oi:b', pool) == 'oi:b'


@pytest.mark.asyncio
async def test_arena_payload_selects_authorized_models_before_reading_model_params(monkeypatch):
    from open_webui.utils import middleware, models

    denied = {'id': 'private-b', 'owned_by': 'openai', 'info': {'params': {'system': 'private instruction'}}}
    allowed = {'id': 'allowed-a', 'owned_by': 'openai'}
    visible = AsyncMock(return_value=[allowed])
    monkeypatch.setattr(models, 'get_filtered_models', visible)
    monkeypatch.setattr(middleware.random, 'choice', lambda items: items[0])
    seen = []

    class AtParameterBoundary(Exception):
        pass

    def boundary(form, model):
        seen.append(model['id'])
        raise AtParameterBoundary()

    monkeypatch.setattr(middleware, 'apply_params_to_form_data', boundary)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(MODELS={'private-b': denied, 'allowed-a': allowed}))
    )
    user = SimpleNamespace(id='u', role='user')
    with pytest.raises(AtParameterBoundary):
        await middleware.process_chat_payload(
            request,
            {'model': 'arena'},
            user,
            {},
            {'id': 'arena', 'owned_by': 'arena', 'info': {'meta': {'model_ids': ['private-b', 'allowed-a']}}},
        )
    assert seen == ['allowed-a']
    visible.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'role,admin_bypass,expected_bypass', [('user', True, False), ('admin', False, False), ('admin', True, True)]
)
async def test_arena_fallback_filters_permissions_and_preserves_access_policy(
    db, monkeypatch, role, admin_bypass, expected_bypass
):
    from open_webui.utils import chat, models
    from open_webui.utils.oi import legacy

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(chat, 'get_async_db', session)
    monkeypatch.setattr(legacy, 'alias_map', AsyncMock(return_value={}))
    monkeypatch.setattr(chat, 'BYPASS_MODEL_ACCESS_CONTROL', False)
    monkeypatch.setattr(chat, 'BYPASS_ADMIN_ACCESS_CONTROL', admin_bypass)
    allowed = {'id': 'allowed-a', 'pipe': {'id': 'synthetic'}, 'owned_by': 'openai'}
    arena = {
        'id': 'arena',
        'owned_by': 'arena',
        'arena': True,
        'info': {'meta': {'model_ids': ['private-b', 'allowed-a']}},
    }
    pool = {'arena': arena, 'private-b': {'id': 'private-b', 'owned_by': 'openai'}, 'allowed-a': allowed}
    monkeypatch.setattr(models, 'get_filtered_models', AsyncMock(return_value=[allowed]))
    monkeypatch.setattr(chat.random, 'choice', lambda items: items[0])

    checked = []

    async def access(user, model, **kwargs):
        checked.append(model['id'])
        if model['id'] == 'private-b':
            raise HTTPException(403, 'synthetic denied')

    monkeypatch.setattr(chat, 'check_model_access', access)

    async def track(request, form, user, dispatch, *args, **kwargs):
        return await dispatch('call')

    monkeypatch.setattr(chat, 'track_call', track)

    async def provider(request, form, user, models):
        return {'actual_model': form['model'], 'bypass': request.state.bypass_filter}

    monkeypatch.setattr(chat, 'generate_function_chat_completion', provider)
    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS=pool)))
    result = await chat.generate_chat_completion(
        request, {'model': 'arena', 'messages': []}, SimpleNamespace(id='u', role=role)
    )
    assert result['actual_model'] == 'allowed-a' and result['selected_model_id'] == 'allowed-a'
    assert result['bypass'] is expected_bypass
    assert ('allowed-a' in checked) is (not expected_bypass)


@pytest.mark.asyncio
async def test_arena_browser_members_require_the_requesting_users_live_session(monkeypatch):
    from open_webui.utils import models
    from open_webui.socket import main as socket
    from open_webui.utils.oi.model_capabilities import accessible_arena_candidates

    user = SimpleNamespace(id='u', role='user')
    browser = {'id': 'oi:browser', 'direct': True}
    server = {'id': 'oi:server'}
    pool = {item['id']: item for item in [browser, server]}
    monkeypatch.setattr(models, 'get_filtered_models', AsyncMock(return_value=[browser, server]))
    monkeypatch.setattr(socket, 'SESSION_POOL', {'live': {'id': 'u'}, 'foreign': {'id': 'other'}})
    assert await accessible_arena_candidates(pool, user, metadata={'session_id': 'live'}) == ['oi:browser', 'oi:server']
    assert await accessible_arena_candidates(pool, user, metadata={'session_id': 'foreign'}) == ['oi:server']
    with pytest.raises(HTTPException) as denied:
        await accessible_arena_candidates(pool, user, members=['oi:browser'])
    assert denied.value.status_code == 422
    assert pool['oi:browser'] is browser  # Filtering never mutates the shared global model pool.


@pytest.mark.asyncio
async def test_arena_permission_candidates_use_current_connection_state(db, monkeypatch):
    from open_webui.models.customization import Connection, ModelBinding
    from open_webui.models.models import Models
    from open_webui.models.groups import Groups
    from open_webui.models.access_grants import AccessGrants
    from open_webui.internal import db as internal_db
    from open_webui.utils.oi import catalog
    from open_webui.utils.oi.model_capabilities import accessible_arena_candidates

    @asynccontextmanager
    async def session(db=None):
        yield test_db

    test_db = db
    monkeypatch.setattr(internal_db, 'get_async_db_context', session)
    monkeypatch.setattr(Models, 'get_model_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(AccessGrants, 'get_accessible_resource_ids', AsyncMock(return_value=[]))
    for key, shared in [('a', True), ('b', False)]:
        db.add(
            Connection(
                id=key,
                owner_id='owner',
                name='Same connection',
                template='openai-compatible',
                protocol='openai-chat',
                transport='server',
                base_url='https://synthetic.invalid/v1',
                credentials='',
                settings={},
                shared=shared,
                enabled=True,
                revision=1,
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            ModelBinding(
                id='oi:' + key,
                connection_id=key,
                upstream_id='same-model',
                name='Same model',
                protocol='openai-chat',
                enabled=True,
                available=True,
                capabilities={},
                pricing={},
                params={},
                aliases=[],
                updated_at=1,
            )
        )
    await db.flush()
    pool = {
        m['id']: m for m in await catalog.model_catalog(db, SimpleNamespace(id='owner', role='admin'), internal=True)
    }
    user = SimpleNamespace(id='u', role='user')
    assert await accessible_arena_candidates(pool, user) == ['oi:a']
    (await db.get(Connection, 'a')).enabled = False
    await db.flush()
    # The stale global pool still contains A, but the authoritative directory revokes it.
    with pytest.raises(HTTPException):
        await accessible_arena_candidates(pool, user)
