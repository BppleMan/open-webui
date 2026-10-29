"""Synthetic HTTP -> native adapter -> real tool executor -> follow-up HTTP."""

import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.models.customization import ModelCall
from open_webui.utils.middleware import execute_tool_call, process_messages_with_output
from open_webui.utils.misc import convert_output_to_messages
from open_webui.utils.oi import runtime
from open_webui.utils.oi.protocols import generate
from open_webui.utils.oi.replay import attach_latest_native
from sqlalchemy import select

from test.oi.test_review_regressions import binding, connection


def provider_response(protocol, payloads, req):
    payloads.append(json.loads(req.content))
    turn = len(payloads)
    if protocol == 'openai-chat':
        message = {'role': 'assistant', 'content': 'done' if turn == 3 else '', 'reasoning_content': f'signed-{turn}'}
        if turn < 3:
            message['tool_calls'] = [
                {
                    'index': 0,
                    'id': f'tool-{turn}',
                    'type': 'function',
                    'function': {'name': 'lookup', 'arguments': json.dumps({'q': str(turn)})},
                }
            ]
        frames = [
            {
                'choices': [{'index': 0, 'delta': message, 'finish_reason': 'tool_calls' if turn < 3 else 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 3},
            }
        ]
        return httpx.Response(200, content=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames))
    if protocol == 'openai-responses':
        output = [{'type': 'reasoning', 'id': f'r-{turn}', 'summary': [], 'encrypted_content': f'signed-{turn}'}]
        output += (
            [
                {
                    'type': 'function_call',
                    'id': f'fc-{turn}',
                    'call_id': f'tool-{turn}',
                    'name': 'lookup',
                    'arguments': json.dumps({'q': str(turn)}),
                }
            ]
            if turn < 3
            else [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'done'}]}]
        )
        frames = [
            {
                'type': 'response.completed',
                'response': {
                    'id': f'r-{turn}',
                    'status': 'completed',
                    'output': output,
                    'usage': {'input_tokens': 10, 'output_tokens': 3},
                },
            }
        ]
        return httpx.Response(200, content=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames))
    if protocol == 'gemini-interactions':
        steps = [{'type': 'thought', 'signature': f'signed-{turn}', 'summary': []}]
        steps += (
            [{'type': 'function_call', 'id': f'tool-{turn}', 'name': 'lookup', 'arguments': {'q': str(turn)}}]
            if turn < 3
            else [{'type': 'model_output', 'content': [{'type': 'text', 'text': 'done'}]}]
        )
        frames = [{'event_type': 'interaction.created', 'interaction': {'id': f'i-{turn}', 'status': 'in_progress'}}]
        for index, step in enumerate(steps):
            frames += [
                {'event_type': 'step.start', 'index': index, 'step': step},
                {'event_type': 'step.stop', 'index': index},
            ]
        frames += [
            {
                'event_type': 'interaction.completed',
                'interaction': {
                    'status': 'requires_action' if turn < 3 else 'completed',
                    'usage': {'total_input_tokens': 10, 'total_output_tokens': 3},
                },
            }
        ]
        return httpx.Response(200, content=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames))
    if protocol == 'ollama':
        tools = (
            [{'function': {'index': i, 'name': 'lookup', 'arguments': {'q': f'{turn}-{i}'}}} for i in range(2)]
            if turn < 3
            else []
        )
        body = {
            'message': {
                'role': 'assistant',
                'content': 'done' if turn == 3 else '',
                'thinking': 'reason',
                'tool_calls': tools,
            },
            'done': True,
            'done_reason': 'stop',
            'prompt_eval_count': 10,
            'eval_count': 3,
        }
        return httpx.Response(200, content=json.dumps(body) + '\n')
    if protocol == 'anthropic':
        blocks = [{'type': 'thinking', 'thinking': f'reason-{turn}', 'signature': f'signed-{turn}'}]
        blocks += (
            [{'type': 'tool_use', 'id': f'tool-{turn}', 'name': 'lookup', 'input': {'q': str(turn)}}]
            if turn < 3
            else [{'type': 'text', 'text': 'done'}]
        )
        frames = [{'type': 'message_start', 'message': {'content': [], 'usage': {'input_tokens': 10}}}]
        for i, block in enumerate(blocks):
            frames += [
                {'type': 'content_block_start', 'index': i, 'content_block': block},
                {'type': 'content_block_stop', 'index': i},
            ]
        frames += [
            {
                'type': 'message_delta',
                'delta': {'stop_reason': 'tool_use' if turn < 3 else 'end_turn'},
                'usage': {'output_tokens': 3},
            },
            {'type': 'message_stop'},
        ]
    else:
        parts = [{'thought': True, 'text': f'reason-{turn}', 'thoughtSignature': f'history-thought-{turn}'}]
        parts += (
            [
                {
                    'functionCall': {'id': f'tool-{turn}', 'name': 'lookup', 'args': {'q': str(turn)}},
                    'thoughtSignature': f'signed-{turn}',
                }
            ]
            if turn < 3
            else [{'text': 'done'}]
        )
        frames = [
            {
                'candidates': [{'content': {'role': 'model', 'parts': parts}, 'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 3},
            }
        ]
    return httpx.Response(200, content=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames))


def assert_followup_history(protocol, payloads):
    for turn, payload in enumerate(payloads[1:], start=2):
        encoded = json.dumps(payload)
        for previous in range(1, turn):
            if protocol != 'ollama':
                assert f'signed-{previous}' in encoded
        if protocol == 'cpa-agy':
            assert 'history-thought-' not in encoded
        assert 'result:' in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'protocol,transport_kind',
    [
        (p, 'server')
        for p in ['openai-chat', 'openai-responses', 'anthropic', 'gemini', 'gemini-interactions', 'cpa-agy', 'ollama']
    ]
    + [(p, 'browser') for p in ['openai-chat', 'openai-responses', 'anthropic', 'gemini']],
)
async def test_two_tool_rounds_keep_native_history_and_charge_each_call_once(db, monkeypatch, protocol, transport_kind):
    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    writes = AsyncMock()
    monkeypatch.setattr('open_webui.models.chats.Chats.upsert_message_to_chat_by_id_and_message_id', writes)
    tool_inputs, payloads, events = [], [], []

    async def lookup(q: str):
        tool_inputs.append(q)
        return 'result:' + q

    metadata = {
        'chat_id': 'temporary:socket',
        'oi_temporary_id': 'stable-rp-session',
        'message_id': 'response',
        'tools': {
            'lookup': {
                'callable': lookup,
                'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
            }
        },
    }
    user = SimpleNamespace(id='u')
    await runtime.start_generation(user, metadata, 'nested-preset')
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    b, c = binding(protocol), connection(protocol)

    transport = httpx.MockTransport(lambda req: provider_response(protocol, payloads, req))
    if transport_kind == 'browser':
        from open_webui.socket import main as socket
        from open_webui.utils.oi.browser_transport import BrowserTransport

        c.transport, c.owner_id, c.enabled, c.revision = 'browser', user.id, True, 1
        metadata['session_id'] = 'synthetic-session'
        monkeypatch.setattr('open_webui.models.config.Config.get', AsyncMock(return_value=True))
        monkeypatch.setattr(socket, 'SESSION_POOL', {'synthetic-session': {'id': user.id}})
        monkeypatch.setattr(socket.sio, 'emit', AsyncMock())

        async def relay(event):
            data = event['data']
            assert data['protocol'] == protocol and data['stream'] is True
            if protocol == 'gemini':
                assert 'stream' not in data['form_data']
            response = provider_response(protocol, payloads, httpx.Request('POST', data['url'], json=data['form_data']))
            queue = socket.EVENT_QUEUES[data['channel']]
            await queue.put(response.content)
            await queue.put({'done': True})
            return {'status_code': 200, 'headers': {'content-type': 'text/event-stream'}}

        monkeypatch.setattr(socket, 'get_event_call', AsyncMock(return_value=relay))
        transport = BrowserTransport(user, metadata, b, c)
    messages = [{'role': 'user', 'content': 'Use lookup twice then answer.'}]
    for turn in range(1, 4):
        form = {'model': 'nested-preset', 'messages': copy.deepcopy(messages), 'stream': True, 'metadata': metadata}
        response = await runtime.track_call(
            request, form, user, lambda call_id: generate(request, form, user, b, c, call_id, transport), b, c
        )
        parser, current = runtime.SSEDecoder(), []
        async for raw in response.body_iterator:
            current.extend(parser.feed(raw))
        events.extend(current)
        tools = [
            tool
            for event in current
            for choice in event.get('choices', [])
            for tool in choice.get('delta', {}).get('tool_calls', [])
        ]
        if protocol == 'openai-responses':
            tools = [
                {
                    'id': item['call_id'],
                    'type': 'function',
                    'function': {'name': item['name'], 'arguments': item['arguments']},
                }
                for event in current
                for item in event.get('response', {}).get('output', [])
                if item.get('type') == 'function_call'
            ]
        if turn == 3:
            assert not tools
            break
        output = []
        for tool in tools:
            params, result, *_ = await execute_tool_call(form, metadata, None, tool)
            assert result == 'result:' + params['q']
            output.extend(
                [
                    {
                        'type': 'function_call',
                        'status': 'completed',
                        'call_id': tool['id'],
                        'name': tool['function']['name'],
                        'arguments': tool['function']['arguments'],
                    },
                    {
                        'type': 'function_call_output',
                        'call_id': tool['id'],
                        'output': [{'type': 'input_text', 'text': result}],
                    },
                ]
            )
        tool_messages = convert_output_to_messages(output, raw=True, flatten_tool_images=True)
        attach_latest_native(request, metadata, tool_messages)
        messages = process_messages_with_output([*messages, *tool_messages])
    assert len(payloads) == 3
    assert len(tool_inputs) == (4 if protocol == 'ollama' else 2)
    assert_followup_history(protocol, payloads)
    if protocol == 'ollama':
        assert any(
            choice.get('delta', {}).get('reasoning_content') == 'reason'
            for event in events
            for choice in event.get('choices', [])
        )
        calls = [
            choice['delta']['tool_calls']
            for event in events
            for choice in event.get('choices', [])
            if choice.get('delta', {}).get('tool_calls')
        ]
        assert all([tool['index'] for tool in batch] == [0, 1] for batch in calls)
    rows = (await db.execute(select(ModelCall))).scalars().all()
    assert len(rows) == 3 and len({row.id for row in rows}) == 3
    assert all(row.status == 'completed' for row in rows)
    assert all(row.usage['input_tokens'] == 10 and row.usage['output_tokens'] == 3 for row in rows)
    writes.assert_not_called()  # Temporary response text/signatures never went to chat persistence.
