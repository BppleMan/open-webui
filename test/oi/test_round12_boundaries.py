"""Opaque terminal slots, strict live identity and non-executable terminal tools."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from open_webui.utils.oi.replay import native_function_slots, remap_stream_tool
from open_webui.utils.oi.runtime import SSEDecoder
from test.oi import test_round12_terminal_reasoning as terminal_tests
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['anthropic', 'gemini', 'openai-responses', 'gemini-interactions'])
@pytest.mark.parametrize('limited', [False, True])
@pytest.mark.parametrize('reasoning_format', [None, 'thinking'])
@pytest.mark.parametrize('tool_images', [False, True])
async def test_opaque_terminal_without_visible_output_stays_after_result(
    monkeypatch, protocol, limited, reasoning_format, tool_images
):
    original = terminal_tests.terminal_response

    def opaque_response(protocol, req, mode, limited):
        response = original(protocol, req, mode, limited)
        frames = [json.loads(line[6:]) for line in response.content.decode().splitlines() if line.startswith('data: ')]
        for frame in frames:
            if protocol == 'anthropic' and frame.get('type') == 'content_block_start':
                frame['content_block'] = {'type': 'redacted_thinking', 'data': 'signed-3'}
            elif protocol == 'gemini':
                for part in frame['candidates'][0]['content']['parts']:
                    part.pop('text', None)
            elif protocol == 'gemini-interactions' and frame.get('event_type') == 'step.start':
                frame['step'].pop('summary', None)
        return httpx.Response(200, content=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames))

    monkeypatch.setattr(terminal_tests, 'terminal_response', opaque_response)
    await terminal_tests.test_terminal_thoughts_do_not_move_before_completed_tools(
        monkeypatch, protocol, 'reasoning_only', limited, reasoning_format, tool_images=tool_images
    )


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_live_native_identity_remains_strict_until_that_call_is_invalidated(protocol):
    raw = provider_response(protocol, [], httpx.Request('POST', 'https://synthetic.invalid', json={}))
    state = protocols.NativeStream(protocol)
    if protocol == 'ollama':
        state.nonstream(json.loads(raw.content))
        state.ollama_tools_with_ids(state.body['message']['tool_calls'])
    else:
        for event in SSEDecoder().feed(raw.content):
            state.feed(event)
    native = {'protocol': protocol, 'body': state.body}
    slots = native_function_slots(native)
    assert slots
    index, provider_id, _ = slots[0]
    before = copy.deepcopy(state.body)
    request = SimpleNamespace(state=SimpleNamespace(oi_native_streams={'active': state}))
    response = SimpleNamespace(headers={'x-oi-native-call-id': 'active'})
    with pytest.raises(ValueError, match='Native tool does not match'):
        remap_stream_tool(request, response, index, 'unrelated-id', 'canonical')
    state.replay_invalidated = True
    remap_stream_tool(request, response, index, 'filtered-unrelated-id', 'canonical')
    assert state.body == before
    state.replay_invalidated = False
    with pytest.raises(ValueError, match='Native tool does not match'):
        remap_stream_tool(request, response, index, 'filtered-unrelated-id', 'canonical')


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('status', ['incomplete', 'failed'])
async def test_unsuccessful_responses_tools_are_retained_but_never_dispatched(monkeypatch, streaming, status):
    saved, events, ctx = context(monkeypatch, 'openai-responses')
    ctx['form_data']['stream'] = streaming
    executed = []

    async def lookup(q):
        executed.append(q)
        return 'unexpected execution'

    ctx['metadata']['tools'] = {
        'lookup': {'callable': lookup, 'spec': {'name': 'lookup', 'parameters': {'type': 'object'}}}
    }
    call = {
        'type': 'function_call',
        'id': 'fc',
        'call_id': 'call',
        'name': 'lookup',
        'arguments': '{"q":"no"}',
        'status': 'completed',
    }
    body = {'status': status, 'output': [call], 'usage': {'input_tokens': 10, 'output_tokens': 2}}
    if status == 'incomplete':
        body['incomplete_details'] = {'reason': 'max_output_tokens'}
    else:
        body['error'] = {'message': 'synthetic failure'}
    transport = httpx.MockTransport(
        lambda _: (
            httpx.Response(
                200, content='data: ' + json.dumps({'type': 'response.' + status, 'response': body}) + '\n\n'
            )
            if streaming
            else httpx.Response(200, json=body)
        )
    )
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding('openai-responses'),
        connection('openai-responses'),
        'terminal-tool',
        transport,
    )
    await middleware.process_chat_response(response, ctx)
    assert not executed
    assert saved['done'] and saved['response_status'] == status
    assert saved['output'][0]['status'] == 'incomplete'
    assert saved['oi_native'][0]['body'] == body
    history = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([{**saved, 'role': 'assistant'}]))
    _, payload = protocols.build_request(
        {'messages': history}, binding('openai-responses'), connection('openai-responses')
    )
    assert 'lookup' not in json.dumps(payload)
