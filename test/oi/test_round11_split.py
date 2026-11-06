"""Exercise the existing concatenated-JSON tool compatibility path through native replay."""

import json
import httpx
import pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from test.oi.round10_helpers import text_response
from test.oi.test_round9_protocols import context
from test.oi.test_round10_tools import pairs
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['openai-chat', 'openai-responses'])
@pytest.mark.parametrize('split', [0, 1, 2])
async def test_supported_split_tool_arguments_keep_executed_history(monkeypatch, protocol, split):
    saved, events, ctx = context(monkeypatch, protocol)
    arguments = '{"q":"one"}' + ('{"q":"two"}' if split else '') + ('{"q":"three"}' if split == 2 else '')
    requests, executed = [], []

    async def lookup(q):
        executed.append(q)
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    def wire(req):
        requests.append(json.loads(req.content))
        if len(requests) > 1:
            return text_response(protocol, ['finished'])
        if protocol == 'openai-chat':
            event = {
                'choices': [
                    {
                        'index': 0,
                        'delta': {
                            'role': 'assistant',
                            'content': 'prefix',
                            'tool_calls': [
                                {
                                    'index': 0,
                                    'id': 't1',
                                    'type': 'function',
                                    'function': {'name': 'lookup', 'arguments': arguments},
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ]
            }
        else:
            event = {
                'type': 'response.completed',
                'response': {
                    'status': 'completed',
                    'output': [
                        {
                            'type': 'message',
                            'id': 'm1',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': 'prefix'}],
                        },
                        {
                            'type': 'function_call',
                            'id': 'fc1',
                            'call_id': 't1',
                            'name': 'lookup',
                            'arguments': arguments,
                        },
                    ],
                },
            }
        return httpx.Response(200, content='data: ' + json.dumps(event) + '\n\n')

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding(protocol),
            connection(protocol),
            'split-' + str(len(requests)),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    saved.update(role='assistant', model='oi:leaf')
    errors = []
    expected = ['one', 'two', 'three'] if split == 2 else ['one', 'two'] if split else ['one']
    if executed != expected or len(requests) != 2:
        errors.append({'stage': 'execution', 'executed': executed, 'requests': len(requests)})
    if len(requests) > 1:
        calls, results = pairs(protocol, requests[-1])
        if len(calls) != len(expected) or len(results) != len(expected):
            errors.append({'stage': 'immediate', 'calls': calls, 'results': results})
    payload = None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
        calls, results = pairs(protocol, payload)
        if len(calls) != len(expected) or len(results) != len(expected):
            errors.append({'stage': 'saved-history', 'calls': calls, 'results': results})
    except Exception as exc:
        errors.append({'stage': 'saved-history', 'error': f'{type(exc).__name__}: {exc}'})
    assert not errors, errors
