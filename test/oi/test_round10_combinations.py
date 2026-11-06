"""Adversarial consumer combinations; real adapters, filters, tools and capsules."""

import asyncio
import copy
import json
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.utils import middleware, filter as filters
from open_webui.utils.oi import protocols
from open_webui.utils.oi.native_privacy import replay_envelope
from open_webui.utils.oi.temporary_native import restore
from test.oi.round10_helpers import PROTOCOLS, text_response
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_review_regressions import binding, connection
from test.oi.test_round9_protocols import context
from test.oi.test_round10_tools import pairs


def install_filter(monkeypatch, transform):
    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(filters, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(
        middleware, 'get_filter_functions', AsyncMock(return_value=[SimpleNamespace(id='synthetic-filter')])
    )
    monkeypatch.setattr(filters, 'get_function_module', AsyncMock(return_value=SimpleNamespace(stream=transform)))


async def consume(monkeypatch, protocol, pieces, *, initial=None, temporary=False, transform=None):
    saved, events, ctx = context(
        monkeypatch, protocol, initial, **({'assistant_message_id': 'reply'} if initial else {})
    )
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='temporary-fixture')
        if initial:
            ctx['request'].state.oi_temporary_native_history = {'reply': copy.deepcopy(initial['oi_native'])}
    if transform:
        install_filter(monkeypatch, transform)
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'continued' if initial else 'first',
        httpx.MockTransport(lambda _: text_response(protocol, pieces)),
    )
    await middleware.process_chat_response(response, ctx)
    if temporary:
        final = next(event['data'] for event in reversed(events) if event.get('data', {}).get('done'))
        saved = {**final, 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        restore(saved, 'u', 'temporary-fixture')
    saved.update(id='reply', role='assistant', model='oi:leaf')
    return saved


def outbound(saved, protocol, portable=False):
    candidate = copy.deepcopy(saved)
    if portable:
        candidate['oi_native'] = [replay_envelope(n) for n in candidate['oi_native']]
    messages = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([candidate]))
    return protocols.build_request({'messages': messages}, binding(protocol), connection(protocol))[1]


SAMPLES = [
    '<think>a</think>x<think>b</think>y<think>c</think>z',
    '<reason>a</reason>x<think>b</think>y<reason>c</reason>z',
    '<think>a</think><|begin_of_solution|>x<|end_of_solution|>y<think>b</think>z',
    '<think>a<think>b</think>c</think>answer',
    'prefix<think mood="q">a</think>x<thinking>b</thinking>answer',
    '<thinking>a</thinking><think>b</think><reason>c</reason>answer',
    '<think>a</think><think>b</think><think>c</think>answer',
    '<think>a</think>answer',
]


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('sample', range(len(SAMPLES)))
async def test_actual_random_partition_then_continue_and_edit(monkeypatch, protocol, sample):
    raw = SAMPLES[sample]
    rng = random.Random(10 + sample)
    cuts = sorted({0, len(raw), *rng.sample(range(1, len(raw)), min(12, len(raw) - 1))})
    pieces = [raw[a:b] for a, b in zip(cuts, cuts[1:])]
    saved = await consume(monkeypatch, protocol, pieces, temporary=bool(sample % 2))
    saved = await consume(monkeypatch, protocol, [' next'], initial=saved, temporary=bool(sample % 2))
    before = copy.deepcopy(saved)
    for portable in [False, True]:
        body = outbound(saved, protocol, portable)
        assert json.dumps(raw)[1:-1] in json.dumps(body) and 'next' in json.dumps(body)
    assert saved == before
    target = next((item for item in saved['output'] if item.get('type') == 'reasoning'), saved['output'][0])
    (target.get('content') or target['summary'])[0]['text'] += ' edited'
    with pytest.raises(ValueError, match='saved output'):
        outbound(saved, protocol)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
async def test_metering_only_stream_filter_keeps_native_valid(monkeypatch, protocol):
    saved, events, ctx = context(monkeypatch, protocol)

    async def transform(event):
        event['usage'] = {'prompt_tokens': 321, 'completion_tokens': 9}
        return event

    install_filter(monkeypatch, transform)
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'metering',
        httpx.MockTransport(lambda _: text_response(protocol, ['unchanged'])),
    )
    await middleware.process_chat_response(response, ctx)
    saved.update(role='assistant', model='oi:leaf')
    native = saved['oi_native'][0]
    assert native['replayable'] and not native.get('consumer_invalidated')
    assert 'unchanged' in json.dumps(outbound(saved, protocol))


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('change', ['arguments', 'name'])
async def test_stream_filter_tool_edits_govern_execution_and_replay(monkeypatch, protocol, change):
    saved, events, ctx = context(monkeypatch, protocol)
    payloads, executed = [], []

    async def lookup(q):
        executed.append(q)
        return 'result:' + q

    ctx['metadata']['tools'] = {
        name: {
            'callable': lookup,
            'spec': {'name': name, 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
        for name in ['lookup', 'lookup_filtered']
    }

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if not isinstance(value, dict):
            return value
        value = {k: rewrite(v) for k, v in value.items()}
        if change == 'name' and value.get('name') == 'lookup':
            value['name'] = 'lookup_filtered'
        if change == 'arguments' and 'arguments' in value:
            arguments = value['arguments']
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            if isinstance(args, dict) and 'q' in args:
                args = {**args, 'q': 'filtered-' + args['q']}
                value['arguments'] = json.dumps(args) if isinstance(arguments, str) else args
        return value

    async def transform(event):
        return rewrite(event)

    install_filter(monkeypatch, transform)

    def wire(req):
        result = provider_response(protocol, payloads, req)
        return httpx.Response(
            200, content=result.content.replace(b'tool-1', b'repeated').replace(b'tool-2', b'repeated')
        )

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
    await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    assert len(payloads) == 3 and len(executed) == (4 if protocol == 'ollama' else 2)
    assert all(q.startswith('filtered-') for q in executed) is (change == 'arguments')
    saved.update(role='assistant', model='oi:leaf')
    for payload in [*payloads[1:], outbound(saved, protocol), outbound(saved, protocol, True)]:
        calls, results = pairs(protocol, payload)
        assert len(calls) == len(results)
        assert all(('lookup_filtered' if change == 'name' else 'filtered-') in json.dumps(call) for call in calls)
    for native in saved['oi_native'][:2]:
        assert native['consumer_invalidated'] and not native['replayable']
        assert replay_envelope(native)['body'] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('repeated_item_id', [False, True])
async def test_responses_added_done_completed_share_call_scoped_identity(monkeypatch, repeated_item_id):
    saved, events, ctx = context(monkeypatch, 'openai-responses')
    payloads, executed = [], []

    async def lookup(q):
        executed.append(q)
        return q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    def wire(req):
        response = provider_response('openai-responses', payloads, req)
        event = json.loads(response.content.decode()[6:])
        output = event['response']['output']
        frames = []
        for index, item in enumerate(output):
            if item.get('type') == 'function_call':
                item['call_id'] = 'provider-reused'
                if repeated_item_id:
                    item['id'] = 'item-reused'
                added = {**item, 'arguments': ''}
                frames.extend(
                    [
                        {'type': 'response.output_item.added', 'output_index': index, 'item': added},
                        {
                            'type': 'response.function_call_arguments.delta',
                            'output_index': index,
                            'item_id': item['id'],
                            'delta': item['arguments'],
                        },
                        {'type': 'response.output_item.done', 'output_index': index, 'item': item},
                    ]
                )
        frames.append(event)
        return httpx.Response(200, content=''.join('data: ' + json.dumps(f) + '\n\n' for f in frames))

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding('openai-responses'),
            connection('openai-responses'),
            'call-' + str(len(payloads) + 1),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    assert executed == ['1', '2'] and len(payloads) == 3
    saved.update(role='assistant', model='oi:leaf')
    for payload in [payloads[-1], outbound(saved, 'openai-responses'), outbound(saved, 'openai-responses', True)]:
        calls, results = pairs('openai-responses', payload)
        assert len(calls) == len(results) == 2 and [c[0] for c in calls] == results
        assert 'signed-1' in json.dumps(payload) and 'signed-2' in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
async def test_cancel_after_stream_filter_never_replays_original(monkeypatch, protocol):
    saved, events, ctx = context(monkeypatch, protocol)

    async def transform(event):
        return json.loads(json.dumps(event).replace('original', 'filtered'))

    install_filter(monkeypatch, transform)
    emitted = False
    original_emitter = ctx['event_emitter']

    async def emit(event):
        nonlocal emitted
        if not emitted and 'filtered' in json.dumps(event):
            emitted = True
            raise asyncio.CancelledError()
        await original_emitter(event)

    ctx['event_emitter'] = emit
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'cancelled',
        httpx.MockTransport(lambda _: text_response(protocol, ['original', 'suffix'])),
    )
    with pytest.raises(asyncio.CancelledError):
        await middleware.process_chat_response(response, ctx)
    saved.update(role='assistant', model='oi:leaf')
    assert emitted and 'filtered' in json.dumps(saved['output'])
    payload = outbound(saved, protocol)
    assert 'original' not in json.dumps(payload) and 'filtered' in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('temporary', [False, True])
async def test_filtered_call_then_continue_keeps_only_canonical_text(monkeypatch, protocol, temporary):
    async def transform(event):
        return json.loads(json.dumps(event).replace('synthetic-original', 'synthetic-filtered'))

    saved = await consume(monkeypatch, protocol, ['synthetic-original'], temporary=temporary, transform=transform)
    saved = await consume(monkeypatch, protocol, [' continued'], initial=saved, temporary=temporary)
    assert len(saved['oi_native']) == 2
    assert saved['oi_native'][0]['replayable'] is False
    assert saved['oi_native'][1]['replayable'] is True
    for portable in [False, True]:
        payload = json.dumps(outbound(saved, protocol, portable))
        assert 'synthetic-filtered' in payload and 'continued' in payload and 'synthetic-original' not in payload
