"""Saved/temporary seven-protocol tool flow across six completion/cancellation boundaries."""

import asyncio
import copy
import json
import httpx
import pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from open_webui.utils.oi.temporary_native import restore
from test.oi.round10_helpers import PROTOCOLS, text_response
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_round10_tools import pairs
from test.oi.test_review_regressions import binding, connection

PHASES = ['complete', 'before_tools', 'inside_first', 'before_followup', 'inside_followup', 'inside_second']


def partial_response(protocol):
    if protocol != 'openai-responses':
        return text_response(protocol, ['partial-followup', ' unconsumed'])
    item = {'type': 'message', 'id': 'partial-message', 'role': 'assistant', 'status': 'in_progress', 'content': []}
    frames = [
        {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
        {
            'type': 'response.content_part.added',
            'item_id': item['id'],
            'output_index': 0,
            'content_index': 0,
            'part': {'type': 'output_text', 'text': '', 'annotations': []},
        },
        {
            'type': 'response.output_text.delta',
            'item_id': item['id'],
            'output_index': 0,
            'content_index': 0,
            'delta': 'partial-followup',
        },
        {
            'type': 'response.output_text.delta',
            'item_id': item['id'],
            'output_index': 0,
            'content_index': 0,
            'delta': ' unconsumed',
        },
        {
            'type': 'response.completed',
            'response': {
                'status': 'completed',
                'output': [
                    {
                        **item,
                        'status': 'completed',
                        'content': [{'type': 'output_text', 'text': 'partial-followup unconsumed'}],
                    }
                ],
            },
        },
    ]
    return httpx.Response(200, content=''.join(('data: ' + json.dumps(f) + '\n\n' for f in frames)))


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('temporary', [False, True])
async def test_tool_lifecycle_preserves_completed_pairs(monkeypatch, protocol, phase, temporary):
    saved, events, ctx = context(monkeypatch, protocol)
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='lifecycle-temp')
    payloads, executed = ([], [])
    did_cancel = False

    async def lookup(q):
        nonlocal did_cancel
        executed.append(q)
        if not did_cancel and (
            phase == 'inside_first' and len(executed) == 1 or (phase == 'inside_second' and q.startswith('2'))
        ):
            did_cancel = True
            raise asyncio.CancelledError()
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }
    real_execute = middleware.execute_tool_call

    async def execute(*args):
        nonlocal did_cancel
        if phase == 'before_tools' and (not did_cancel):
            did_cancel = True
            raise asyncio.CancelledError()
        return await real_execute(*args)

    monkeypatch.setattr(middleware, 'execute_tool_call', execute)
    emit_original = ctx['event_emitter']

    async def emit(event):
        nonlocal did_cancel
        if phase == 'inside_followup' and (not did_cancel) and ('partial-followup' in json.dumps(event)):
            did_cancel = True
            raise asyncio.CancelledError()
        await emit_original(event)

    ctx['event_emitter'] = emit

    def wire(req):
        if phase == 'inside_followup' and len(payloads) == 1:
            payloads.append(json.loads(req.content))
            return partial_response(protocol)
        return provider_response(protocol, payloads, req)

    async def generate(request, form, user, **kwargs):
        nonlocal did_cancel
        if phase == 'before_followup' and len(payloads) == 1 and (not did_cancel):
            did_cancel = True
            raise asyncio.CancelledError()
        return await protocols.generate(
            request,
            form,
            user,
            binding(protocol),
            connection(protocol),
            'lifecycle-' + str(len(payloads)),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    response = await generate(ctx['request'], ctx['form_data'], ctx['user'])
    if phase == 'complete':
        await middleware.process_chat_response(response, ctx)
    else:
        with pytest.raises(asyncio.CancelledError):
            await middleware.process_chat_response(response, ctx)
        assert did_cancel
    if temporary:
        terminal = next(
            (
                e['data']
                for e in reversed(events)
                if e['type'] == 'chat:tasks:cancel'
                or (e['type'] == 'chat:completion' and e.get('data', {}).get('done'))
            )
        )
        saved = {**copy.deepcopy(terminal), 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        assert restore(saved, 'u', 'lifecycle-temp'), (phase, events)
    else:
        saved.update(id='reply', role='assistant', model='oi:leaf')
    completed = [
        i
        for i in saved['output']
        if i.get('type') == 'function_call_output' and i.get('status') in {'completed', 'failed', 'rejected'}
    ]
    expected = (
        0
        if phase in {'before_tools', 'inside_first'}
        else (4 if protocol == 'ollama' else 2)
        if phase == 'complete'
        else 2
        if protocol == 'ollama'
        else 1
    )
    assert len(completed) == expected, (phase, saved)
    error, payload = (None, None)
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
        calls, results = pairs(protocol, payload)
        if len(calls) != expected or len(results) != expected:
            error = {'completed_pairs_lost': expected, 'calls': calls, 'results': results}
        if phase == 'inside_followup' and 'partial-followup' not in json.dumps(payload):
            error = {'partial_text_lost': True, 'previous_error': error}
        if expected and protocol != 'ollama' and ('signed-1' not in json.dumps(payload)):
            error = {'prior_signature_lost': True, 'previous_error': error}
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert error is None, error
