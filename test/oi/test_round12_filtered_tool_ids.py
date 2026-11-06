"""Actual stream filter ID rewrite composed with repeated-provider-ID recovery."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from open_webui.utils import middleware, filter as filters
from open_webui.utils.oi import protocols
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_round10_tools import pairs
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('rewrite_ids', [False, True, 'first', 'second'])
@pytest.mark.parametrize('duplicate', [False, True])
async def test_stream_id_rewrite_and_duplicate_id_recovery_compose(monkeypatch, protocol, rewrite_ids, duplicate):
    saved, events, ctx = context(monkeypatch, protocol)
    payloads, executed, remap_errors = ([], [], [])
    original_remap = protocols.NativeStream.remap_tool_id

    def trace_remap(state, index, old_id, new_id):
        try:
            return original_remap(state, index, old_id, new_id)
        except Exception as exc:
            remap_errors.append(
                {
                    'protocol': state.protocol,
                    'index': index,
                    'old_id': old_id,
                    'replay_invalidated': state.replay_invalidated,
                    'error': str(exc),
                }
            )
            raise

    monkeypatch.setattr(protocols.NativeStream, 'remap_tool_id', trace_remap)

    async def lookup(q):
        executed.append(q)
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if not isinstance(value, dict):
            return value
        return {
            k: 'filtered-' + v
            if (
                rewrite_ids is True
                or rewrite_ids == 'first'
                and len(payloads) == 1
                or rewrite_ids == 'second'
                and len(payloads) == 2
            )
            and k in {'id', 'call_id'}
            and isinstance(v, str)
            and v.startswith('tool-')
            else rewrite(v)
            for k, v in value.items()
        }

    async def transform(event):
        return rewrite(event)

    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(filters, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(
        middleware, 'get_filter_functions', AsyncMock(return_value=[SimpleNamespace(id='synthetic-id-filter')])
    )
    monkeypatch.setattr(filters, 'get_function_module', AsyncMock(return_value=SimpleNamespace(stream=transform)))

    def wire(req):
        raw = provider_response(protocol, payloads, req)
        content = raw.content
        if protocol == 'ollama':
            body = json.loads(content)
            for i, tool in enumerate(body['message'].get('tool_calls', [])):
                tool['id'] = f'tool-repeat-{i}' if duplicate else f'tool-{len(payloads)}-{i}'
            content = (json.dumps(body) + '\n').encode()
        elif duplicate:
            content = content.replace(b'tool-1', b'tool-repeat').replace(b'tool-2', b'tool-repeat')
        return httpx.Response(200, content=content)

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding(protocol),
            connection(protocol),
            'round-' + str(len(payloads)),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    errors = []
    if len(payloads) != 3 or len(executed) != (4 if protocol == 'ollama' else 2):
        errors.append({'stage': 'execution', 'requests': len(payloads), 'executed': executed})
    saved.update(role='assistant', model='oi:leaf')
    before = copy.deepcopy(saved)
    rebuilt = None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, rebuilt = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
        calls, results = pairs(protocol, rebuilt)
        if len(calls) != len(results) or len(calls) != (4 if protocol == 'ollama' else 2):
            errors.append({'stage': 'history', 'calls': calls, 'results': results})
    except Exception as exc:
        errors.append({'stage': 'history', 'error': f'{type(exc).__name__}: {exc}'})
    assert saved == before
    assert not errors, errors
    assert not remap_errors, remap_errors
    edited = copy.deepcopy(saved)
    next(item for item in edited['output'] if item.get('type') == 'function_call')['arguments'] = '{"q":"tampered"}'
    with pytest.raises(ValueError, match='Native'):
        middleware.process_messages_with_output([edited])
