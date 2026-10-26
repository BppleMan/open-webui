from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from open_webui.models.customization import Connection, ModelBinding
from open_webui.models.models import ModelParams, Models
from open_webui.utils.access_control import has_base_model_access
from open_webui.utils.oi.model_targets import resolve_native_target
from open_webui.utils.oi.protocols import build_request


def preset(id, base, **params):
    return SimpleNamespace(id=id, base_model_id=base, is_active=True, params=ModelParams(**params), user_id='u')


async def native(db):
    db.add(
        Connection(
            id='connection',
            owner_id='u',
            name='Claude connection',
            template='anthropic',
            protocol='anthropic',
            base_url='https://claude.test/v1',
            credentials='',
            settings={},
            shared=False,
            enabled=True,
            revision=1,
            created_at=1,
            updated_at=1,
        )
    )
    db.add(
        ModelBinding(
            id='oi:leaf',
            connection_id='connection',
            upstream_id='same-model',
            protocol='anthropic',
            name='Same name',
            enabled=True,
            available=True,
            capabilities={'output_limit': 4096},
            pricing={'rates': {'input': '2'}},
            params={'temperature': 0.5},
            aliases=['old-id'],
            updated_at=1,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_preset_routes_native_leaf_with_inherited_params_and_price(db, monkeypatch):
    await native(db)
    rows = {
        'rp': preset('rp', 'nested', temperature=0.7, system='Explicit preset instruction'),
        'nested': preset('nested', 'oi:leaf', max_tokens=2000),
    }

    async def lookup(id, **kwargs):
        return rows.get(id)

    monkeypatch.setattr(Models, 'get_model_by_id', lookup)
    target = await resolve_native_target(db, 'rp', SimpleNamespace(id='u', role='user'))
    assert target.binding.id == 'oi:leaf' and target.connection.id == 'connection'
    assert target.params == {'temperature': 0.7, 'max_tokens': 2000, 'system': 'Explicit preset instruction'}
    assert target.binding.pricing['rates']['input'] == '2'
    url, payload = build_request(
        {'model': 'rp', 'messages': [{'role': 'user', 'content': 'Hello'}]}, target.binding, target.connection
    )
    assert url == 'https://claude.test/v1/messages'
    assert payload['model'] == 'same-model'
    assert await has_base_model_access('u', rows['nested'], user_role='user', db=db)
    assert not await has_base_model_access('other', rows['nested'], user_role='user', db=db)


@pytest.mark.asyncio
async def test_preset_cannot_reach_disabled_or_invisible_connection(db, monkeypatch):
    await native(db)

    async def lookup(id, **kwargs):
        return preset('rp', 'oi:leaf') if id == 'rp' else None

    monkeypatch.setattr(Models, 'get_model_by_id', lookup)
    with pytest.raises(HTTPException) as error:
        await resolve_native_target(db, 'rp', SimpleNamespace(id='other', role='user'))
    assert error.value.status_code == 404
    (await db.get(Connection, 'connection')).enabled = False
    with pytest.raises(HTTPException):
        await resolve_native_target(db, 'rp', SimpleNamespace(id='u', role='user'))


@pytest.mark.asyncio
async def test_preset_alias_ambiguity_and_cycles_fail(db, monkeypatch):
    await native(db)
    rows = {'rp': preset('rp', 'old-id'), 'cycle': preset('cycle', 'cycle')}

    async def lookup(id, **kwargs):
        return rows.get(id)

    monkeypatch.setattr(Models, 'get_model_by_id', lookup)
    assert (await resolve_native_target(db, 'rp', SimpleNamespace(id='u', role='user'))).binding.id == 'oi:leaf'
    with pytest.raises(HTTPException) as error:
        await resolve_native_target(db, 'cycle', SimpleNamespace(id='u', role='user'))
    assert error.value.status_code == 409
    db.add(
        ModelBinding(
            id='oi:other',
            connection_id='connection',
            upstream_id='other-model',
            protocol='anthropic',
            name='Other',
            enabled=True,
            available=True,
            capabilities={},
            pricing={},
            params={},
            aliases=['old-id'],
            updated_at=1,
        )
    )
    await db.flush()
    with pytest.raises(HTTPException) as error:
        await resolve_native_target(db, 'rp', SimpleNamespace(id='u', role='user'))
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_dispatch_preserves_preset_identity_and_explicit_request_params(db, monkeypatch):
    from contextlib import asynccontextmanager

    from open_webui.utils import chat
    from open_webui.utils.oi import protocols
    from starlette.requests import Request

    await native(db)

    async def lookup(id, **kwargs):
        return preset('rp', 'oi:leaf', temperature=0.7, max_tokens=2000) if id == 'rp' else None

    monkeypatch.setattr(Models, 'get_model_by_id', lookup)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(chat, 'get_async_db', session)
    monkeypatch.setattr(chat, 'check_model_access', AsyncMock())
    recorded = {}

    async def track(request, form, user, dispatch, binding, connection):
        recorded.update(model_id=form['model'], binding_id=binding.id, price=binding.pricing)
        return await dispatch('synthetic-call')

    async def generate(request, form, user, binding, connection, call_id):
        return build_request(form, binding, connection)

    monkeypatch.setattr(chat, 'track_call', track)
    monkeypatch.setattr(protocols, 'generate', generate)
    request = Request(
        {
            'type': 'http',
            'app': SimpleNamespace(state=SimpleNamespace(MODELS={'rp': {'id': 'rp', 'oi_base_binding': 'oi:leaf'}})),
        }
    )
    form = {'model': 'rp', 'messages': [{'role': 'user', 'content': 'Hello'}], 'temperature': 0.9}
    url, body = await chat.generate_chat_completion(request, form, SimpleNamespace(id='u', role='user'))
    assert url.endswith('/messages') and body['temperature'] == 0.9 and body['max_tokens'] == 2000
    assert recorded['model_id'] == 'rp' and recorded['binding_id'] == 'oi:leaf'
    assert form == {'model': 'rp', 'messages': [{'role': 'user', 'content': 'Hello'}], 'temperature': 0.9}
