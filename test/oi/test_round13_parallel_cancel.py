"""Cancellation inside a later tool must preserve earlier executed results from the same batch."""

import asyncio
import copy
import json
import httpx
import pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from open_webui.utils.oi.temporary_native import restore
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_round10_tools import pairs
from test.oi.test_review_regressions import binding, connection


def parallel_response(protocol, req):
    raw = provider_response(protocol, [], req)
    if protocol == 'ollama':
        body = json.loads(raw.content)
        for i, t in enumerate(body['message']['tool_calls']):
            t['id'] = 'tool-' + str(i + 1)
            t['function']['arguments'] = {'q': str(i + 1)}
        return httpx.Response(200, content=json.dumps(body) + '\n')
    frames = [json.loads(line[6:]) for line in raw.content.decode().splitlines() if line.startswith('data: ')]
    if protocol == 'openai-chat':
        first = frames[0]['choices'][0]['delta']['tool_calls'][0]
        second = copy.deepcopy(first)
        second.update(index=1, id='tool-2')
        second['function']['arguments'] = '{"q":"2"}'
        frames[0]['choices'][0]['delta']['tool_calls'].append(second)
    elif protocol == 'openai-responses':
        second = copy.deepcopy(frames[0]['response']['output'][-1])
        second.update(id='fc-2', call_id='tool-2', arguments='{"q":"2"}')
        frames[0]['response']['output'].append(second)
    elif protocol == 'anthropic':
        extra = {
            'type': 'content_block_start',
            'index': 2,
            'content_block': {'type': 'tool_use', 'id': 'tool-2', 'name': 'lookup', 'input': {'q': '2'}},
        }
        frames[-2:-2] = [extra, {'type': 'content_block_stop', 'index': 2}]
    elif protocol == 'gemini-interactions':
        frames[-1:-1] = [
            {
                'event_type': 'step.start',
                'index': 2,
                'step': {'type': 'function_call', 'id': 'tool-2', 'name': 'lookup', 'arguments': {'q': '2'}},
            },
            {'event_type': 'step.stop', 'index': 2},
        ]
    else:
        frames[0]['candidates'][0]['content']['parts'].append(
            {
                'functionCall': {'id': 'tool-2', 'name': 'lookup', 'args': {'q': '2'}},
                'thoughtSignature': 'signed-second',
            }
        )
    return httpx.Response(200, content=''.join('data: ' + json.dumps(f) + '\n\n' for f in frames))


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('cancel_at', [None, '1', '2'])
@pytest.mark.parametrize('temporary', [False, True])
@pytest.mark.parametrize('delegates', [False, True])
async def test_prior_results_survive_same_batch_later_cancellation(
    monkeypatch, protocol, cancel_at, temporary, delegates
):
    saved, events, ctx = context(monkeypatch, protocol)
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='parallel-temp')
    executed, payloads = [], []
    first_finished = asyncio.Event()

    async def lookup(q):
        executed.append(q)
        if delegates and q == '2':
            await first_finished.wait()
        if q == cancel_at:
            raise asyncio.CancelledError()
        if q == '1':
            first_finished.set()
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }
    if delegates:
        ctx['metadata']['tools']['delegate_task'] = ctx['metadata']['tools'].pop('lookup')
        ctx['metadata']['tools']['delegate_task']['spec']['name'] = 'delegate_task'

    def wire(req):
        payloads.append(json.loads(req.content))
        response = (
            parallel_response(protocol, req) if len(payloads) == 1 else provider_response(protocol, [{}, {}], req)
        )
        return (
            httpx.Response(response.status_code, content=response.content.replace(b'lookup', b'delegate_task'))
            if delegates
            else response
        )

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding(protocol),
            connection(protocol),
            'parallel-' + str(len(payloads)),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    response = await generate(ctx['request'], ctx['form_data'], ctx['user'])
    if cancel_at:
        with pytest.raises(asyncio.CancelledError):
            await middleware.process_chat_response(response, ctx)
    else:
        await middleware.process_chat_response(response, ctx)
    if temporary:
        final = next(
            e['data']
            for e in reversed(events)
            if e['type'] == 'chat:tasks:cancel' or (e['type'] == 'chat:completion' and e.get('data', {}).get('done'))
        )
        saved = {**copy.deepcopy(final), 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        assert restore(saved, 'u', 'parallel-temp')
    else:
        saved.update(id='reply', role='assistant', model='oi:leaf')
    expected = 2 if cancel_at is None else 0 if cancel_at == '1' else 1
    completed = [
        i for i in saved['output'] if i.get('type') == 'function_call_output' and i.get('status') == 'completed'
    ]
    issues = []
    if len(completed) != expected:
        issues.append({'lost_completed_results': expected - len(completed), 'executed': executed})
    payload = None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
        calls, outcomes = pairs(protocol, payload)
        if len(calls) != expected or len(outcomes) != expected:
            issues.append({'history_pairs': len(calls), 'history_results': len(outcomes), 'expected': expected})
    except Exception as e:
        issues.append({'rebuild_error': str(e)})
    assert executed == (['1'] if cancel_at == '1' and not delegates else ['1', '2'])
    assert not issues, issues
