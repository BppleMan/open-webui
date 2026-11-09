from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
import pytest
import httpx
from fastapi import FastAPI
from open_webui.internal.db import get_async_session
from open_webui.routers.customization import router
from open_webui.utils.auth import get_verified_user
from open_webui.utils import models
from open_webui.models.customization import ModelBinding
from open_webui.models.models import ModelModel


async def directory(db, monkeypatch, overrides=()):
    @asynccontextmanager
    async def isolated():
        yield db

    monkeypatch.setattr('open_webui.internal.db.get_async_db', isolated)
    monkeypatch.setattr(models.Config, 'get_many', AsyncMock(return_value={}))
    monkeypatch.setattr(models, 'get_all_base_models', AsyncMock(return_value=[]))
    monkeypatch.setattr(models, 'ENABLE_FUNCTIONS', False)
    monkeypatch.setattr(models.Models, 'get_all_models', AsyncMock(return_value=list(overrides)))
    monkeypatch.setattr(models.Functions, 'get_functions_by_ids', AsyncMock(return_value=[]))
    monkeypatch.setattr(models.Functions, 'get_function_valves_by_ids', AsyncMock(return_value={}))
    monkeypatch.setattr(models, 'get_functions_cache', lambda request: {})
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={}, BASE_MODELS=[], redis=None)))
    return await models.get_all_models(req, user=SimpleNamespace(id='bob', role='user'))


def client_for(db):
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='alice', role='user')
    app.dependency_overrides[get_async_session] = lambda: db
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://local/api/v1/'
    )


async def create(client, **extra):
    c = await client.post(
        'connections',
        json={
            'name': 'Private',
            'template': 'openai',
            'base_url': 'https://synthetic.invalid',
            'shared': False,
            **extra,
        },
    )
    assert c.status_code == 200, c.text
    b = await client.post(
        'model-bindings',
        json={'connection_id': c.json()['id'], 'upstream_id': 'synthetic', 'name': 'Private', 'params': {}},
    )
    assert b.status_code == 200, b.text
    return c.json(), b.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, [], 'bad'])
async def test_saved_binding_controls_rejected(db, monkeypatch, value):
    async with client_for(db) as client:
        c, b = await create(client)
        response = await client.put(
            'model-bindings/' + b['id'],
            json={
                'connection_id': c['id'],
                'upstream_id': 'synthetic',
                'name': 'Private',
                'params': {'model_controls': value},
            },
        )
        assert response.status_code == 422, response.text
        await directory(db, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['params', 'default_params', 'model_controls'])
async def test_connection_settings_cannot_poison_catalog(db, monkeypatch, field):
    async with client_for(db) as client:
        c, b = await create(client, settings={field: None if field == 'model_controls' else {'model_controls': None}})
        assert len(await directory(db, monkeypatch)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, [], 'bad'])
async def test_legacy_bad_binding_controls_isolated(db, monkeypatch, value):
    async with client_for(db) as client:
        c, b = await create(client)
    row = await db.get(ModelBinding, b['id'])
    row.params = {'model_controls': value}
    await db.commit()
    result = await directory(db, monkeypatch)
    assert result[0]['info']['params']['model_controls'] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'reasoning', [True, {'parameter': '__proto__.effort', 'levels': ['high']}], ids=['bool', 'invalid-path']
)
async def test_browser_discovery_cannot_poison_catalog(db, monkeypatch, reasoning):
    monkeypatch.setattr(models.Config, 'get', AsyncMock(return_value=True))
    async with client_for(db) as client:
        c, b = await create(client, transport='browser')
        response = await client.post(
            'connections/' + c['id'] + '/browser-models',
            json={'revision': 1, 'models': [{'id': 'synthetic', 'capabilities': {'reasoning': reasoning}}]},
        )
        assert response.status_code in (200, 422), response.text
    await directory(db, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'reasoning', [True, {'parameter': '__proto__.effort', 'levels': ['high']}], ids=['bool', 'invalid-path']
)
async def test_legacy_discovered_reasoning_isolated(db, monkeypatch, reasoning):
    async with client_for(db) as client:
        c, b = await create(client)
    row = await db.get(ModelBinding, b['id'])
    row.capabilities = {'discovered': {'reasoning': reasoning}, 'overrides': {}}
    await db.commit()
    await directory(db, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize('custom', [None, 'My description'])
@pytest.mark.parametrize('reverse', [False, True])
async def test_directory_keeps_catalog_description_for_overrides_and_presets(db, monkeypatch, custom, reverse):
    async with client_for(db) as client:
        _, item = await create(client)
    binding = await db.get(ModelBinding, item['id'])
    binding.capabilities = {**binding.capabilities, 'catalog_metadata': {'description': 'From gateway', 'tags': []}}
    await db.flush()
    fields = dict(user_id='u', name='My name', params={}, is_active=True, updated_at=1, created_at=1)
    records = [
        ModelModel(id=binding.id, meta={'description': custom}, **fields),
        ModelModel(id='preset', base_model_id=binding.id, meta={'description': 'Preset description'}, **fields),
        ModelModel(id='inherited', base_model_id=binding.id, meta={}, **fields),
    ]
    if reverse:
        records.reverse()
    result = {m['id']: m['info']['meta'].get('description') for m in await directory(db, monkeypatch, records)}
    assert result[binding.id] == (custom or 'From gateway')
    assert result['preset'] == 'Preset description'
    assert result['inherited'] == result[binding.id]
