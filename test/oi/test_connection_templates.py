"""Exercise template creation through HTTP, persistence, model identity and native dispatch."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from open_webui.internal.db import get_async_session
from open_webui.models.customization import Connection, ModelBinding
from open_webui.routers.customization import router
from open_webui.utils.auth import get_verified_user
from open_webui.utils.oi import catalog
from open_webui.utils.oi.connection_templates import TEMPLATES, TEMPLATE_VERSION
from open_webui.utils.oi.protocols import build_request
from sqlalchemy import select


@pytest_asyncio.fixture
async def api(db):
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='template-user', role='admin')
    app.dependency_overrides[get_async_session] = lambda: db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test/api/v1/') as client:
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'template,protocol,path,header',
    [
        ('openai', 'openai-responses', '/responses', 'Authorization'),
        ('anthropic', 'anthropic', '/messages', 'x-api-key'),
        ('gemini', 'gemini', '/models/same-model:generateContent', 'x-goog-api-key'),
    ],
)
async def test_template_defaults_reach_native_dispatch(api, db, template, protocol, path, header):
    created = await api.post(
        'connections', json={'name': 'My connection', 'template': template, 'api_key': 'synthetic'}
    )
    assert created.status_code == 200, created.text
    value = created.json()
    assert value['protocol'] == protocol
    assert value['settings']['template_version'] == TEMPLATE_VERSION
    assert 'synthetic' not in created.text and 'credentials' not in value
    response = await api.post(
        'model-bindings',
        json={
            'connection_id': value['id'],
            'name': 'Display name',
            'upstream_id': 'same-model',
            'protocol': protocol,
            'capabilities': {'output_limit': 1000},
        },
    )
    assert response.status_code == 200, response.text
    binding = await db.get(ModelBinding, response.json()['id'])
    connection = await db.get(Connection, value['id'])
    url, body = build_request(
        {'messages': [{'role': 'user', 'content': 'Hello'}], 'stream': False}, binding, connection
    )
    assert url.endswith(path)
    assert header in catalog.request_headers(connection)
    assert 'Display name' not in str(body)


@pytest.mark.asyncio
async def test_repeat_template_connections_keep_independent_models_and_overrides(api, db, monkeypatch):
    ids = []
    for name in ['Gemini A', 'Gemini B']:
        response = await api.post(
            'connections', json={'name': name, 'template': 'gemini', 'base_url': 'https://custom.test/v1beta'}
        )
        assert response.status_code == 200
        ids.append(response.json()['id'])
        for model in ['model-one', 'model-two']:
            result = await api.post(
                'model-bindings',
                json={
                    'connection_id': ids[-1],
                    'upstream_id': model,
                    'name': model,
                    'protocol': 'gemini',
                    'pricing': {'rates': {'input': '1.23'}},
                    'params': {'temperature': 0.7},
                },
            )
            assert result.status_code == 200, result.text
    models = (await api.get('model-bindings')).json()
    assert len({m['id'] for m in models}) == 4
    assert ids[0] != ids[1]
    selected = next(t for t in TEMPLATES if t['id'] == 'gemini')
    monkeypatch.setitem(selected, 'base_url', 'https://changed-template.test/v9')
    monkeypatch.setitem(selected, 'version', TEMPLATE_VERSION + 1)
    edit = await api.put(
        f'connections/{ids[0]}',
        json={
            'name': 'Renamed A',
            'template': 'gemini',
            'revision': 1,
        },
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()['base_url'] == 'https://custom.test'
    assert edit.json()['settings']['template_version'] == TEMPLATE_VERSION
    updated = (await api.get('model-bindings')).json()
    for model in models:
        if model['connection_id'] == ids[0]:
            model['connection_name'] = 'Renamed A'
    assert updated == models
    stale = await api.put(f'connections/{ids[0]}', json={'name': 'stale', 'template': 'gemini', 'revision': 1})
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_unknown_template_and_required_fields_are_rejected(api, db):
    assert (await api.post('connections', json={'name': 'Bad', 'template': 'not-a-template'})).status_code == 422
    assert (
        await api.post(
            'connections', json={'name': 'Azure', 'template': 'azure-openai', 'base_url': 'https://resource.test'}
        )
    ).status_code == 422
    assert not (await db.execute(select(Connection))).scalars().all()
    response = await api.post(
        'connections',
        json={
            'name': 'Azure',
            'template': 'azure-openai',
            'base_url': 'https://resource.test',
            'settings': {'api_version': 'synthetic-version'},
        },
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['settings']['auth'] == 'api-key'
    assert (await api.post(f'connections/{value["id"]}/discover')).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'anthropic'])
async def test_discovery_collects_all_pages_before_updating_availability(db, protocol):
    c = Connection(
        id='c',
        owner_id='u',
        name='c',
        template=protocol,
        protocol=protocol,
        base_url='https://provider.test/v1',
        credentials='',
        settings={},
        shared=False,
        enabled=True,
        revision=1,
        created_at=1,
        updated_at=1,
    )
    db.add(c)
    await db.flush()
    requests = []

    def handler(request):
        requests.append(request)
        if protocol == 'gemini':
            return httpx.Response(
                200,
                json={'models': [{'name': 'models/second'}]}
                if request.url.params.get('pageToken') == 'p2'
                else {'models': [{'name': 'models/first'}], 'nextPageToken': 'p2'},
            )
        return httpx.Response(
            200,
            json={'data': [{'id': 'second', 'display_name': 'Second'}], 'has_more': False}
            if request.url.params.get('after_id') == 'first'
            else {'data': [{'id': 'first'}], 'has_more': True, 'last_id': 'first'},
        )

    rows = await catalog.discover(db, c, httpx.MockTransport(handler))
    assert len(requests) == 2
    assert {r.upstream_id for r in rows} == {'first', 'second'}
    assert all(r.available and not r.enabled for r in rows)
    if protocol == 'anthropic':
        assert next(r for r in rows if r.upstream_id == 'second').name == 'Second'
    with pytest.raises(ValueError):
        await catalog.discover(
            db, c, httpx.MockTransport(lambda _: httpx.Response(200, json={'error': 'not a model list'}))
        )
    assert all(r.available for r in rows)


@pytest.mark.asyncio
async def test_model_edits_keep_discovery_and_overrides_separate(api, db):
    created = (await api.post('connections', json={'name': 'Discover', 'template': 'openai'})).json()
    c = await db.get(Connection, created['id'])
    rows = await catalog.discover(
        db,
        c,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    'data': [{'id': 'test', 'capabilities': {'vision': False, 'input_limit': 1000}}],
                },
            )
        ),
    )
    await db.commit()
    row = rows[0]
    response = await api.put(
        f'model-bindings/{row.id}',
        json={
            'connection_id': c.id,
            'name': 'My label',
            'upstream_id': 'test',
            'protocol': c.protocol,
            'capabilities': {'vision': False, 'input_limit': 1000},
            'capability_overrides': {'input_limit': 900},
        },
    )
    assert response.status_code == 200
    await catalog.discover(
        db,
        c,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    'data': [{'id': 'test', 'capabilities': {'vision': True, 'input_limit': 2000}}],
                },
            )
        ),
    )
    assert catalog.capabilities(row) == {'vision': True, 'input_limit': 900}
    assert row.name == 'My label'


@pytest.mark.asyncio
async def test_failed_later_page_never_replaces_existing_models(api, db):
    created = (await api.post('connections', json={'name': 'Discover', 'template': 'gemini'})).json()
    c = await db.get(Connection, created['id'])
    original = await catalog.discover(
        db,
        c,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    'models': [{'name': 'models/existing'}],
                },
            )
        ),
    )
    await db.commit()

    def handler(request):
        if request.url.params.get('pageToken'):
            return httpx.Response(503)
        return httpx.Response(200, json={'models': [{'name': 'models/new'}], 'nextPageToken': 'p2'})

    with pytest.raises(httpx.HTTPStatusError):
        await catalog.discover(db, c, httpx.MockTransport(handler))
    saved = (await db.execute(select(ModelBinding))).scalars().all()
    assert saved == original and saved[0].available
