"""Actual native adapter, stream consumer, tool executor and immediate follow-up."""

import copy
import json

import httpx
import pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection
from test.oi.test_native_tool_roundtrip import provider_response

PROTOCOLS = ['openai-chat', 'openai-responses', 'anthropic', 'gemini', 'gemini-interactions', 'cpa-agy', 'ollama']


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from dictionaries(v)
    elif isinstance(value, list):
        for v in value:
            yield from dictionaries(v)


def pairs(protocol, payload):
    calls, results = [], []
    if protocol in {'openai-chat', 'ollama'}:
        for m in payload['messages']:
            for call in m.get('tool_calls', []):
                calls.append((call.get('id'), call['function']))
            if m['role'] == 'tool':
                results.append(m.get('tool_call_id'))
    elif protocol == 'anthropic':
        for obj in dictionaries(payload['messages']):
            if obj.get('type') == 'tool_use':
                calls.append((obj['id'], obj))
            if obj.get('type') == 'tool_result':
                results.append(obj['tool_use_id'])
    elif protocol in {'gemini', 'cpa-agy'}:
        for obj in dictionaries(payload['contents']):
            if obj.get('functionCall'):
                calls.append((obj['functionCall'].get('id'), obj['functionCall']))
            if obj.get('functionResponse'):
                results.append(obj['functionResponse'].get('id'))
    else:
        for obj in dictionaries(payload['input']):
            if obj.get('type') == 'function_call':
                calls.append((obj.get('call_id') or obj.get('id'), obj))
            if obj.get('type') in {'function_result', 'function_call_output'}:
                results.append(obj['call_id'])
    return calls, results


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('duplicate', [False, True])
async def test_repeated_provider_tool_ids_roundtrip(monkeypatch, protocol, duplicate):
    saved, events, ctx = context(monkeypatch, protocol)
    payloads, tool_inputs = [], []

    async def lookup(q: str):
        tool_inputs.append(q)
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    def wire(req):
        response = provider_response(protocol, payloads, req)
        content = response.content
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
            'call-' + str(len(payloads) + 1),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    response = await generate(ctx['request'], ctx['form_data'], ctx['user'])
    await middleware.process_chat_response(response, ctx)
    problems = []
    if len(payloads) != 3 or len(tool_inputs) != (4 if protocol == 'ollama' else 2):
        problems.append({'stage': 'execution', 'requests': len(payloads), 'tool_inputs': tool_inputs})
    for index, payload in enumerate(payloads[1:], 1):
        calls, results = pairs(protocol, payload)
        # Ollama has no provider IDs; compare cardinality there.
        if not len(calls) == len(results) == index * (2 if protocol == 'ollama' else 1):
            problems.append({'stage': 'immediate-follow-up', 'request': index + 1, 'calls': calls, 'results': results})
        elif protocol != 'ollama' and [call[0] for call in calls] != results:
            problems.append({'stage': 'immediate-identity', 'request': index + 1, 'calls': calls, 'results': results})
    # The same history must also survive persistence and reconstruction.
    saved.update(role='assistant', model='oi:leaf')
    original = copy.deepcopy(saved)
    history_payload, history_error = None, None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, history_payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
        calls, results = pairs(protocol, history_payload)
        if len(calls) != len(results) or len(calls) != (4 if protocol == 'ollama' else 2):
            problems.append({'stage': 'saved-history', 'calls': calls, 'results': results})
    except Exception as exc:
        history_error = str(exc)
        problems.append({'stage': 'saved-history', 'error': history_error})
    assert saved == original
    assert not problems, problems
