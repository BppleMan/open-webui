from types import SimpleNamespace

import httpx
import pytest
from open_webui.models.customization import Connection, ModelBinding
from open_webui.models.models import ModelControl
from open_webui.utils.oi.catalog import (
    connection_json,
    credentials,
    discover,
    discovered_capabilities,
    encrypt_credentials,
    reasoning_controls,
    request_headers,
    resolve,
)


def connection(id='a', owner='u', **kwargs):
    return Connection(
        id=id,
        owner_id=owner,
        name=id,
        template='openai',
        protocol='openai-chat',
        base_url='https://example.test/v1',
        credentials='',
        settings={},
        shared=False,
        enabled=True,
        revision=1,
        created_at=1,
        updated_at=1,
        **kwargs,
    )


def binding(connection_id, id, aliases=None):
    return ModelBinding(
        id=id,
        connection_id=connection_id,
        upstream_id='same-model',
        protocol='openai-chat',
        name='Same name',
        enabled=True,
        available=True,
        capabilities={},
        pricing={'rates': {'input': '1'}},
        params={},
        aliases=aliases or [],
        updated_at=1,
    )


@pytest.mark.asyncio
async def test_duplicate_names_route_by_binding_and_ambiguous_aliases_fail(db):
    db.add_all([connection('a'), connection('b'), binding('a', 'oi:a', ['old']), binding('b', 'oi:b', ['old'])])
    await db.flush()
    user = SimpleNamespace(id='u', role='user')
    assert (await resolve(db, 'oi:a', user))[1].id == 'a'
    assert (await resolve(db, 'oi:b', user))[1].id == 'b'
    with pytest.raises(ValueError, match='Ambiguous'):
        await resolve(db, 'old', user)
    with pytest.raises(LookupError):
        await resolve(db, 'oi:a', SimpleNamespace(id='other', role='user'))
    c = await db.get(Connection, 'a')
    c.enabled = False
    with pytest.raises(ValueError, match='unavailable'):
        await resolve(db, 'oi:a', user)


def test_credentials_hidden_and_cpa_identity_is_authoritative():
    c = connection()
    c.credentials = encrypt_credentials({'api_key': 'synthetic-key', 'headers': {'x-rp-chat-id': 'spoof'}})
    assert credentials(c)['api_key'] == 'synthetic-key'
    assert 'synthetic-key' not in str(connection_json(c, SimpleNamespace(id='u', role='user')))
    assert request_headers(c, protocol='anthropic')['anthropic-version'] == '2023-06-01'
    assert request_headers(c, protocol='cpa-agy', user_id='u', chat_id='c')['X-RP-Chat-ID'] == 'c'
    c.template = 'cpa-agy'
    headers = request_headers(c, user_id='u', chat_id='c')
    assert headers['X-RP-Chat-ID'] == 'c'
    assert 'x-rp-chat-id' not in headers
    with pytest.raises(ValueError):
        request_headers(c, user_id='u', chat_id=None)


@pytest.mark.asyncio
async def test_discovery_keeps_prices_params_and_manual_overrides(db):
    c = connection()
    b = binding('a', 'oi:existing')
    b.capabilities = {'discovered': {'vision': False}, 'overrides': {'vision': True}}
    b.params = {'temperature': 0.3}
    db.add_all([c, b])
    await db.flush()
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={'data': [{'id': 'same-model', 'capabilities': {'vision': False}}, {'id': 'new'}]}
        )
    )
    result = await discover(db, c, transport)
    old = next(r for r in result if r.id == b.id)
    assert old.pricing['rates']['input'] == '1'
    assert old.params['temperature'] == 0.3
    assert old.capabilities['overrides']['vision'] is True
    assert next(r for r in result if r.upstream_id == 'new').enabled is False


def test_reasoning_controls_keep_order_and_nested_wire_fields():
    b = binding('a', 'oi:a')
    b.capabilities = {
        'reasoning': {'levels': ['none', 'low', 'high'], 'default': 'low', 'parameter': 'reasoning.effort'}
    }
    control = ModelControl(**reasoning_controls(b)['oi_reasoning'])
    assert control.display == 'slider'
    assert list(control.options) == ['level_0', 'level_1', 'level_2']
    assert control.options[control.default].params == {'reasoning': {'effort': 'low'}}


def test_discovery_reads_advertised_shared_window_without_guessing_reasoning_or_prices():
    result = discovered_capabilities(
        {
            'context_length': 1048576,
            'top_provider': {'context_length': 524288, 'max_completion_tokens': 65536},
            'supported_parameters': ['reasoning', 'reasoning_effort'],
        }
    )
    assert result == {'context_window': 524288, 'output_limit': 65536}


@pytest.mark.parametrize('invalid', [None, True, 0, -1, '1048576', 1.5])
def test_discovery_keeps_invalid_provider_limits_unknown(invalid):
    assert (
        discovered_capabilities(
            {'context_length': invalid, 'top_provider': {'context_length': invalid, 'max_completion_tokens': invalid}}
        )
        == {}
    )


@pytest.mark.asyncio
async def test_refresh_keeps_window_override_and_model_identity(db):
    c = connection()
    b = binding('a', 'oi:existing')
    b.capabilities = {'discovered': {}, 'overrides': {'context_window': 16000}}
    db.add_all([c, b])
    await db.flush()
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={'data': [{'id': 'same-model', 'context_length': 128000}]})
    )
    result = await discover(db, c, transport)
    assert len(result) == 1 and result[0].id == b.id
    assert result[0].capabilities == {
        'discovered': {'context_window': 128000},
        'overrides': {'context_window': 16000},
    }
