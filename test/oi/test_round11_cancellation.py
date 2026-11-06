"""Real adapter/consumer cancellation and temporary native state, synthetic suppliers only."""

import asyncio
import copy
import json

import httpx
import pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from open_webui.utils.oi.temporary_native import restore
from test.oi.round10_helpers import PROTOCOLS, text_response
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection
from test.oi.test_native_tool_roundtrip import provider_response


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['openai-chat', 'openai-responses'])
@pytest.mark.parametrize('complete_arguments', [False, True])
async def test_cancel_during_tool_arguments_keeps_history_usable(monkeypatch, protocol, complete_arguments):
    arguments = '{"q":"partial"}' if complete_arguments else '{"q":"partial'
    saved, events, ctx = context(monkeypatch, protocol)
    if protocol == 'openai-chat':
        frames = [
            {
                'choices': [
                    {'index': 0, 'delta': {'role': 'assistant', 'content': 'saved-prefix'}, 'finish_reason': None}
                ]
            },
            {
                'choices': [
                    {
                        'index': 0,
                        'delta': {
                            'tool_calls': [
                                {
                                    'index': 0,
                                    'id': 't1',
                                    'type': 'function',
                                    'function': {'name': 'lookup', 'arguments': arguments},
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
                'item': {'type': 'message', 'id': 'msg', 'role': 'assistant', 'status': 'in_progress', 'content': []},
            },
            {
                'type': 'response.content_part.added',
                'item_id': 'msg',
                'output_index': 0,
                'content_index': 0,
                'part': {'type': 'output_text', 'text': '', 'annotations': []},
            },
            {
                'type': 'response.output_text.delta',
                'item_id': 'msg',
                'output_index': 0,
                'content_index': 0,
                'delta': 'saved-prefix',
            },
            {
                'type': 'response.output_item.added',
                'output_index': 1,
                'item': {
                    'type': 'function_call',
                    'id': 'fc1',
                    'call_id': 't1',
                    'name': 'lookup',
                    'arguments': '',
                    'status': 'in_progress',
                },
            },
            {'type': 'response.function_call_arguments.delta', 'item_id': 'fc1', 'output_index': 1, 'delta': arguments},
        ]
    original_emit = ctx['event_emitter']
    cancelled = False

    async def emit(event):
        nonlocal cancelled
        if not cancelled and event.get('data', {}).get('delta') == arguments:
            cancelled = True
            raise asyncio.CancelledError()
        await original_emit(event)

    ctx['event_emitter'] = emit
    wire = ''.join('data: ' + json.dumps(f) + '\n\n' for f in frames)
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'cancelled-call',
        httpx.MockTransport(lambda _: httpx.Response(200, content=wire)),
    )
    with pytest.raises(asyncio.CancelledError):
        await middleware.process_chat_response(response, ctx)
    saved.update(role='assistant', model='oi:leaf')
    assert cancelled and saved.get('done')
    assert any(i.get('type') == 'function_call' and i.get('status') == 'incomplete' for i in saved['output'])
    assert not any(i.get('type') == 'function_call_output' for i in saved['output'])
    error, payload = None, None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert error is None, error
    assert 'saved-prefix' in json.dumps(payload)
    assert 'lookup' not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('cancelled_continue', [False, True])
@pytest.mark.parametrize('client_version', ['legacy', 'cancel_snapshot'])
async def test_temporary_continue_cancel_keeps_prior_authenticated_history(
    monkeypatch, protocol, cancelled_continue, client_version
):
    saved, events, ctx = context(monkeypatch, protocol)
    ctx['metadata'].update(chat_id='', oi_temporary_id='cancel-temporary')
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'original-completed-call',
        httpx.MockTransport(lambda req: provider_response(protocol, [{}, {}], req)),
    )
    await middleware.process_chat_response(response, ctx)
    final = next(e['data'] for e in reversed(events) if e.get('data', {}).get('done'))
    client = {**copy.deepcopy(final), 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
    initial = copy.deepcopy(client)
    first_calls = restore(initial, 'u', 'cancel-temporary')
    assert len(first_calls) == 1 and first_calls[0]['replayable']
    saved, continued_events, ctx = context(monkeypatch, protocol, initial, assistant_message_id='reply')
    ctx['metadata'].update(chat_id='', oi_temporary_id='cancel-temporary')
    ctx['request'].state.oi_temporary_native_history = {'reply': first_calls}
    original_emit = ctx['event_emitter']
    did_cancel = False

    async def emit(event):
        nonlocal did_cancel
        if cancelled_continue and not did_cancel and 'continued-prefix' in json.dumps(event):
            did_cancel = True
            raise asyncio.CancelledError()
        await original_emit(event)

    ctx['event_emitter'] = emit
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'continued-call',
        httpx.MockTransport(lambda _: text_response(protocol, ['continued-prefix', ' end'])),
    )
    if cancelled_continue:
        with pytest.raises(asyncio.CancelledError):
            await middleware.process_chat_response(response, ctx)
    else:
        await middleware.process_chat_response(response, ctx)
    # Mirror the observed Chat.svelte handlers: completion updates the capsule;
    # chat:tasks:cancel only replaces output and marks done.
    for event in continued_events:
        data = event.get('data') or {}
        if event['type'] == 'chat:completion':
            if data.get('oi_native_state') and (client_version == 'legacy' or not cancelled_continue):
                client['oi_native_state'] = data['oi_native_state']
            if 'output' in data:
                client['output'] = copy.deepcopy(data['output'])
        elif event['type'] == 'chat:tasks:cancel':
            if client_version == 'cancel_snapshot' and data.get('oi_native_state'):
                client['oi_native_state'] = data['oi_native_state']
            client['output'] = copy.deepcopy(data['output'])
            client['done'] = True
    assert 'continued-prefix' in json.dumps(client['output'])
    request_message = copy.deepcopy(client)
    calls = restore(request_message, 'u', 'cancel-temporary')
    normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([request_message]))
    _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
    # Avoid saving encrypted synthetic capsules unnecessarily.
    assert any(c['call_id'] == 'original-completed-call' for c in calls), (
        'Completed native call disappeared after Continue/cancel'
    )
    assert calls[0]['body'] == first_calls[0]['body']
    corrupted = copy.deepcopy(client)
    corrupted['output'].append({'type': 'message', 'content': [{'type': 'output_text', 'text': 'tampered'}]})
    assert restore(corrupted, 'u', 'cancel-temporary') == [] and corrupted['oi_native_invalidated']


@pytest.mark.parametrize('status', ['completed', 'failed', 'rejected', 'pending', 'queued', 'requires_approval'])
def test_replayable_tool_still_requires_valid_arguments(status):
    from open_webui.utils.oi.replay import consumer_atoms

    with pytest.raises(json.JSONDecodeError):
        consumer_atoms(
            [
                {
                    'type': 'function_call',
                    'call_id': 'strict',
                    'name': 'lookup',
                    'arguments': '{"q":"partial',
                    'status': status,
                }
            ]
        )
