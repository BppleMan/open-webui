"""A transport failure after actual bytes must leave usable, correctly projected history."""

import copy, json
import httpx, pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from open_webui.utils.oi.temporary_native import restore
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_round13_stream_failures import response_for
from test.oi.test_round9_protocols import context
from test.oi.test_round10_tools import pairs
from test.oi.test_review_regressions import binding, connection


class BrokenStream(httpx.AsyncByteStream):
    def __init__(self, content, fail):
        self.content, self.fail = content, fail

    async def __aiter__(self):
        yield self.content
        if self.fail:
            raise httpx.ReadError('synthetic midstream read failure')


async def unexpected_followup(*args, **kwargs):
    raise RuntimeError('unexpected automatic followup; no external request allowed')


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('tools', [False, True])
@pytest.mark.parametrize('failure', ['eof', 'read_error'])
@pytest.mark.parametrize('temporary', [False, True])
async def test_transport_failure_retains_rebuildable_history(monkeypatch, protocol, tools, failure, temporary):
    saved, events, ctx = context(monkeypatch, protocol)
    monkeypatch.setattr(middleware, 'generate_chat_completion', unexpected_followup)
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='transport-temp')
    executed = []

    async def lookup(q):
        executed.append(q)
        return 'unexpected'

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    def wire(req):
        raw = response_for(protocol, req, tools, 'eof')
        return httpx.Response(200, stream=BrokenStream(raw.content, failure == 'read_error'))

    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'broken-call',
        httpx.MockTransport(wire),
    )
    process_error = None
    try:
        await middleware.process_chat_response(response, ctx)
    except Exception as e:
        process_error = str(e)
    if temporary:
        terminal = next(
            (e['data'] for e in reversed(events) if e['type'] == 'chat:completion' and e.get('data', {}).get('done')),
            None,
        )
        saved = {**copy.deepcopy(terminal or {}), 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        if terminal:
            restore(saved, 'u', 'transport-temp')
    else:
        saved.update(id='reply', role='assistant', model='oi:leaf')
    issues = []
    if process_error:
        issues.append({'uncaught_processing_error': process_error})
    if not saved.get('done') or not saved.get('error'):
        issues.append({'missing_final_state': True})
    if executed:
        issues.append({'unexpected_execution': executed})
    normalized, payload = None, None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
        calls, results = pairs(protocol, payload)
        if calls or results:
            issues.append({'undispatched_tools_replayed': calls})
        if not tools and 'done' not in json.dumps(payload):
            issues.append({'partial_body_lost': True})
    except Exception as e:
        issues.append({'rebuild_error': type(e).__name__ + ': ' + str(e)})
    assert not issues, issues


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['openai-chat', 'openai-responses'])
@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('failure', ['eof', 'read_error'])
@pytest.mark.parametrize('temporary', [False, True])
async def test_partial_arguments_with_network_error_do_not_poison_history(
    monkeypatch, protocol, complete, failure, temporary
):
    args = '{"q":"value"}' if complete else '{"q":"value'
    saved, events, ctx = context(monkeypatch, protocol)
    monkeypatch.setattr(middleware, 'generate_chat_completion', unexpected_followup)
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='partial-temp')
    if protocol == 'openai-chat':
        frames = [
            {'choices': [{'delta': {'role': 'assistant', 'content': 'kept-prefix'}, 'finish_reason': None}]},
            {
                'choices': [
                    {
                        'delta': {
                            'tool_calls': [
                                {
                                    'index': 0,
                                    'id': 't',
                                    'type': 'function',
                                    'function': {'name': 'lookup', 'arguments': args},
                                }
                            ]
                        },
                        'finish_reason': None,
                    }
                ]
            },
        ]
    else:
        frames = [
            {
                'type': 'response.output_item.added',
                'output_index': 0,
                'item': {'type': 'message', 'id': 'm', 'role': 'assistant', 'status': 'in_progress', 'content': []},
            },
            {
                'type': 'response.content_part.added',
                'item_id': 'm',
                'output_index': 0,
                'content_index': 0,
                'part': {'type': 'output_text', 'text': ''},
            },
            {
                'type': 'response.output_text.delta',
                'item_id': 'm',
                'output_index': 0,
                'content_index': 0,
                'delta': 'kept-prefix',
            },
            {
                'type': 'response.output_item.added',
                'output_index': 1,
                'item': {
                    'type': 'function_call',
                    'id': 'fc',
                    'call_id': 't',
                    'name': 'lookup',
                    'arguments': '',
                    'status': 'in_progress',
                },
            },
            {'type': 'response.function_call_arguments.delta', 'item_id': 'fc', 'output_index': 1, 'delta': args},
        ]
    raw = ''.join('data: ' + json.dumps(f) + '\n\n' for f in frames).encode()
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'partial-call',
        httpx.MockTransport(lambda _: httpx.Response(200, stream=BrokenStream(raw, failure == 'read_error'))),
    )
    issue = None
    try:
        await middleware.process_chat_response(response, ctx)
    except Exception as e:
        issue = 'processing: ' + str(e)
    if temporary:
        final = next(
            (e['data'] for e in reversed(events) if e['type'] == 'chat:completion' and e.get('data', {}).get('done')),
            None,
        )
        saved = {**copy.deepcopy(final or {}), 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        if final:
            restore(saved, 'u', 'partial-temp')
    else:
        saved.update(id='reply', role='assistant', model='oi:leaf')
    payload = None
    try:
        messages = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': messages}, binding(protocol), connection(protocol))
        assert 'kept-prefix' in json.dumps(payload) and 'lookup' not in json.dumps(payload)
    except Exception as e:
        issue = 'rebuild: ' + type(e).__name__ + ': ' + str(e)
    assert issue is None, issue
