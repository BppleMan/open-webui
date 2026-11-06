from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI
from open_webui.internal.db import get_async_session
from open_webui.routers.customization import router
from open_webui.utils.auth import get_verified_user
from open_webui.utils import models


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'controls',
    [
        None,
        [],
        'invalid',
        {},
        {
            'effort': {
                'label': 'Effort',
                'display': 'menu',
                'options': {'low': {'label': 'Low', 'params': {'reasoning_effort': 'low'}}},
            }
        },
    ],
    ids=['null', 'array', 'string', 'valid-empty', 'valid-control'],
)
async def test_private_invalid_controls_cannot_poison_other_users_catalog(db, monkeypatch, controls):
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='alice', role='user')
    app.dependency_overrides[get_async_session] = lambda: db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test/api/v1/') as client:
        conn = await client.post(
            'connections',
            json={
                'name': 'Private connection',
                'template': 'openai',
                'base_url': 'https://synthetic.invalid',
                'shared': False,
            },
        )
        assert conn.status_code == 200, conn.text
        bound = await client.post(
            'model-bindings',
            json={
                'connection_id': conn.json()['id'],
                'upstream_id': 'synthetic',
                'name': 'Private model',
                'params': {'model_controls': controls},
            },
        )
        if bound.status_code == 422:
            assert not isinstance(controls, dict), bound.text
            return
        assert bound.status_code == 200, bound.text

    @asynccontextmanager
    async def isolated_db():
        yield db

    monkeypatch.setattr('open_webui.internal.db.get_async_db', isolated_db)
    monkeypatch.setattr(models.Config, 'get_many', AsyncMock(return_value={}))
    monkeypatch.setattr(models, 'get_all_base_models', AsyncMock(return_value=[]))
    monkeypatch.setattr(models, 'ENABLE_FUNCTIONS', False)
    monkeypatch.setattr(models.Models, 'get_all_models', AsyncMock(return_value=[]))
    monkeypatch.setattr(models.Functions, 'get_functions_by_ids', AsyncMock(return_value=[]))
    monkeypatch.setattr(models.Functions, 'get_function_valves_by_ids', AsyncMock(return_value={}))
    monkeypatch.setattr(models, 'get_functions_cache', lambda request: {})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={}, BASE_MODELS=[], redis=None)))
    # The shared registry must be buildable for Bob despite Alice's private config.
    registry = await models.get_all_models(request, user=SimpleNamespace(id='bob', role='user'))
    assert len(registry) == 1
    assert registry[0]['id'] == bound.json()['id']
    assert request.app.state.MODELS[bound.json()['id']]['info']['params']['model_controls'] == controls
