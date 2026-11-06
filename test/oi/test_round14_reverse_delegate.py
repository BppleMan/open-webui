"""Different completion order and a cancelled sibling retain the actual completed tool set."""

import asyncio, copy, json
import httpx, pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from open_webui.utils.oi.temporary_native import restore
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_round13_parallel_cancel import parallel_response
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_round10_tools import pairs
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('mode', ['reverse', 'cancel_first', 'normal'])
@pytest.mark.parametrize('temporary', [False, True])
async def test_reverse_delegate_completion_retains_results(monkeypatch, protocol, mode, temporary):
    saved, events, ctx = context(monkeypatch, protocol)
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='reverse-temp')
    second_done = asyncio.Event()
    completed, payloads = [], []

    async def delegate(q):
        if q == '1' and mode != 'normal':
            await second_done.wait()
        if q == '1' and mode == 'cancel_first':
            raise asyncio.CancelledError()
        completed.append(q)
        if q == '2':
            second_done.set()
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'delegate_task': {
            'callable': delegate,
            'spec': {
                'name': 'delegate_task',
                'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}},
            },
        }
    }

    def wire(req):
        payloads.append(json.loads(req.content))
        raw = parallel_response(protocol, req) if len(payloads) == 1 else provider_response(protocol, [{}, {}], req)
        return httpx.Response(200, content=raw.content.replace(b'lookup', b'delegate_task'))

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding(protocol),
            connection(protocol),
            'reverse-' + str(len(payloads)),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    response = await generate(ctx['request'], ctx['form_data'], ctx['user'])
    if mode == 'cancel_first':
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
        assert restore(saved, 'u', 'reverse-temp')
    else:
        saved.update(role='assistant', model='oi:leaf')
    assert completed == (['2'] if mode == 'cancel_first' else ['2', '1'] if mode == 'reverse' else ['1', '2'])
    normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
    _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
    calls, results = pairs(protocol, payload)
    assert len(calls) == len(results) == len(completed)
    for q in completed:
        assert 'result:' + q in json.dumps(payload)
