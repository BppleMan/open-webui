"""R53: templates seed, protocols dispatch, identities survive reconfiguration."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from open_webui.models.customization import Connection, ModelBinding
from open_webui.utils.oi import catalog
from open_webui.utils.oi.connection_templates import TEMPLATES, connection_defaults
from open_webui.routers.customization import ConnectionForm
from open_webui.utils.oi.endpoints import normalize_service_address, resolve_endpoint
from open_webui.utils.oi.protocol_registry import COMMON_PROTOCOLS
from open_webui.utils.oi.protocols import build_request
from open_webui.utils.middleware import process_messages_with_output
from open_webui.utils.oi.selection import snapshot_target
from test.oi.test_connection_templates import api
from test.oi.test_review_regressions import binding, saved_message


def service(base='https://provider.invalid', protocol='openai-chat', template='openai-compatible', settings=None):
    base, settings = normalize_service_address(base, settings or {}, protocol, template)
    return SimpleNamespace(
        id='connection', protocol=protocol, template=template, base_url=base, settings=settings, credentials=''
    )


@pytest.mark.parametrize('template', [t['id'] for t in TEMPLATES])
@pytest.mark.parametrize('mode', COMMON_PROTOCOLS)
def test_every_template_accepts_every_common_mode_without_changing_auth(template, mode):
    form = ConnectionForm(
        name='Synthetic',
        template=template,
        protocol=mode,
        base_url='https://synthetic.invalid',
        settings={'api_version': '2024-06-01', 'auth': 'bearer'},
    )
    defaults = connection_defaults(form)
    assert defaults['protocol'] == mode
    assert defaults['settings']['auth'] == 'bearer'


@pytest.mark.parametrize(
    'base,template,mode,expected',
    [
        ('https://p.test', 'openai-compatible', 'openai-chat', 'https://p.test/v1/chat/completions'),
        ('https://p.test/proxy', 'openai-compatible', 'anthropic', 'https://p.test/proxy/v1/messages'),
        ('https://p.test/proxy/v1', 'openai-compatible', 'openai-responses', 'https://p.test/proxy/v1/responses'),
        (
            'https://p.test/proxy/v1beta',
            'openai-compatible',
            'gemini',
            'https://p.test/proxy/v1beta/models/m:generateContent',
        ),
        ('https://openrouter.ai/api/v1', 'openrouter', 'anthropic', 'https://openrouter.ai/api/v1/messages'),
        ('https://openrouter.ai', 'openrouter', 'anthropic', 'https://openrouter.ai/api/v1/messages'),
        ('https://p.test/proxy/v1', 'cpa-agy', 'gemini', 'https://p.test/proxy/v1beta/models/m:generateContent'),
        ('https://p.test/proxy', 'gemini', 'openai-chat', 'https://p.test/proxy/v1beta/openai/chat/completions'),
        ('https://p.test/api/paas/v4', 'zai', 'openai-chat', 'https://p.test/api/paas/v4/chat/completions'),
    ],
)
def test_service_routes(base, template, mode, expected):
    c = service(base, mode, template)
    assert resolve_endpoint(c, mode, model='m') == expected
    assert resolve_endpoint(c, mode, 'models').endswith('/models')
    if mode == 'gemini':
        assert resolve_endpoint(c, mode, model='m', stream=True) == expected.replace(
            ':generateContent', ':streamGenerateContent?alt=sse'
        )


@pytest.mark.asyncio
async def test_inheritance_override_snapshot_and_discovery_keep_identity(api, db):
    created = (
        await api.post('connections', json={'name': 'Router', 'template': 'openrouter', 'api_key': 'synthetic'})
    ).json()
    cid = created['id']
    original = []
    for override in [None, 'anthropic']:
        r = await api.post(
            'model-bindings',
            json={
                'connection_id': cid,
                'name': 'same',
                'upstream_id': 'same',
                'protocol_override': override,
                'capabilities': {'output_limit': 100},
            },
        )
        assert r.status_code == 200, r.text
        original.append(r.json())
    c = await db.get(Connection, cid)
    old = await db.get(ModelBinding, original[0]['id'])
    frozen, frozen_c = snapshot_target(old, c)
    update = await api.put(
        f'connections/{cid}',
        json={
            'name': 'Router renamed',
            'template': 'openrouter',
            'revision': created['revision'],
            'protocol': 'gemini',
        },
    )
    assert update.status_code == 200, update.text
    assert catalog.request_headers(c, protocol='anthropic')['Authorization'] == 'Bearer synthetic'
    assert 'x-api-key' not in catalog.request_headers(c, protocol='anthropic')
    assert resolve_endpoint(frozen_c, frozen.protocol, model='same').endswith('/api/v1/chat/completions')
    for _ in range(2):
        await catalog.apply_discovered_models(
            db, c, [{'name': 'models/same', 'displayName': 'Provider renamed', 'inputTokenLimit': 1000}]
        )
        await db.commit()
    rows = (await api.get(f'model-bindings?connection_id={cid}')).json()
    assert {m['id'] for m in rows} == {m['id'] for m in original}
    by_id = {m['id']: m for m in rows}
    assert by_id[original[0]['id']]['effective_protocol'] == 'gemini'
    assert by_id[original[0]['id']]['protocol_source'] == 'connection'
    assert by_id[original[1]['id']]['effective_protocol'] == 'anthropic'
    assert by_id[original[1]['id']]['protocol_source'] == 'model'
    assert all(m['name'] == 'same' for m in rows)
    assert all(m['capabilities']['output_limit'] == 100 for m in rows)
    # Native mode overrides affect only chat routing, not embedding endpoints.
    assert (
        resolve_endpoint(c, 'openai-chat', 'capability', path='/embeddings')
        == 'https://openrouter.ai/api/v1/embeddings'
    )


@pytest.mark.asyncio
async def test_preview_is_side_effect_free_and_rejects_unsafe_routes(api, db):
    r = await api.post(
        'connections/resolve',
        json={
            'name': 'preview',
            'template': 'openrouter',
            'protocol': 'anthropic',
            'base_url': 'https://openrouter.ai',
            'api_key': 'not-returned',
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()['generate_url'] == 'https://openrouter.ai/api/v1/messages'
    assert 'not-returned' not in r.text
    assert not (await db.execute(select(Connection))).scalars().all()
    r = await api.post(
        'connections',
        json={
            'name': 'bad',
            'template': 'openai',
            'settings': {'routing': {'prefixes': {'openai-chat': '//other.test'}}},
        },
    )
    assert r.status_code == 422
    assert not (await db.execute(select(Connection))).scalars().all()


def test_incompatible_native_history_is_blocked_and_not_rewritten():
    message = saved_message()
    original = copy.deepcopy(message)
    with pytest.raises(HTTPException) as error:
        build_request(
            {'messages': process_messages_with_output([message])}, binding('gemini'), service(protocol='gemini')
        )
    assert error.value.status_code == 422
    assert message == original


def test_cpa_compatibility_is_independent_of_template_and_auth():
    c = service(protocol='gemini', settings={'routing': {'version': 1, 'profile': 'cpa-agy'}, 'auth': 'bearer'})
    b, frozen = snapshot_target(binding('gemini'), c)
    assert b.protocol == 'cpa-agy'
    headers = catalog.request_headers(frozen, protocol=b.protocol, user_id='owner', chat_id='stable-rp')
    assert headers['X-RP-Chat-ID'] == 'stable-rp'
    assert resolve_endpoint(frozen, b.protocol, model='m').endswith('/v1beta/models/m:generateContent')


def test_azure_responses_uses_service_profile_not_deployment_chat_path():
    c = service('https://resource.invalid/proxy', 'openai-responses', 'azure-openai')
    assert (
        resolve_endpoint(c, 'openai-responses', model='deployment')
        == 'https://resource.invalid/proxy/openai/v1/responses'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', COMMON_PROTOCOLS)
async def test_browser_config_uses_binding_override_and_preserves_auth(api, db, monkeypatch, mode):
    monkeypatch.setattr('open_webui.models.config.Config.get', AsyncMock(return_value=True))
    c = (
        await api.post(
            'connections',
            json={
                'name': 'Direct',
                'template': 'openrouter',
                'transport': 'browser',
                'api_key': 'synthetic-browser',
                'protocol': 'openai-chat',
            },
        )
    ).json()
    b = await api.post(
        'model-bindings',
        json={'name': 'same', 'upstream_id': 'same', 'connection_id': c['id'], 'protocol_override': mode},
    )
    assert b.status_code == 200, b.text
    r = await api.get(f'connections/{c["id"]}/browser-config?revision=1&binding_id={b.json()["id"]}')
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['protocol'] == mode
    assert data['headers']['Authorization'] == 'Bearer synthetic-browser'
    assert 'no-store' in r.headers['cache-control']
    if mode == 'anthropic':
        assert data['endpoints']['generate'] == 'https://openrouter.ai/api/v1/messages'
        assert data['headers']['anthropic-dangerous-direct-browser-access'] == 'true'
    catalog_json = (await api.get('connections')).text
    assert 'synthetic-browser' not in catalog_json
