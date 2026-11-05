"""Protocol-field and tool-identity regressions; synthetic data and MockTransport only."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.utils import filter as filters, middleware
from open_webui.utils.middleware import process_messages_with_output, sanitize_tool_pairs
from open_webui.utils.oi.protocols import NativeStream, build_request
from test.oi.test_review_regressions import binding, connection


def output_text(text):
    return {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['pipeline_inlet', 'inlet', 'request'])
@pytest.mark.parametrize('redact,replacement', [(False, None), (True, 'FILTERED'), (True, '')])
@pytest.mark.parametrize('field', ['thinking', 'reasoning_content', 'reasoning'])
async def test_ollama_thinking_filter_changes_outbound_history(monkeypatch, stage, redact, field, replacement):
    async def modify(body):
        body = copy.deepcopy(body)
        body['temperature'] = 0.3
        if redact:
            # A request filter may explicitly override Ollama's native field.
            # OI's real catalog marks every adapter owned_by=openai, so this
            # field is not initially present in the canonical output projection.
            body['messages'][0][field] = replacement
        return body

    async def pipeline(request, body, user, models):
        return await modify(body) if stage == 'pipeline_inlet' else body

    monkeypatch.setattr(middleware.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_event_emitter', AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(middleware, 'get_event_call', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_filter_functions', AsyncMock(return_value=[SimpleNamespace(id='modify')]))
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', pipeline)
    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(filters, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(filters, 'get_function_module', AsyncMock(return_value=SimpleNamespace(**{stage: modify})))
    model = {
        'id': 'oi:leaf',
        'owned_by': 'openai',
        'oi_binding': True,
        'connection': {'protocol': 'ollama', 'transport': 'server'},
        'info': {'meta': {'capabilities': {'builtin_tools': False}}},
    }
    request = SimpleNamespace(
        state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={'oi:leaf': model}))
    )
    native = {
        'protocol': 'ollama',
        'binding_id': 'oi:leaf',
        'call_id': 'call-1',
        'replayable': True,
        'body': {
            'model': 'synthetic-model',
            'message': {'role': 'assistant', 'content': 'answer', 'thinking': 'private-thought-marker'},
            'done': True,
        },
    }
    saved = {
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'answer',
        'oi_native': [native],
        'output': [
            {'type': 'reasoning', 'summary': [{'type': 'output_text', 'text': 'private-thought-marker'}]},
            output_text('answer'),
        ],
    }
    original = copy.deepcopy(saved)
    form, _, _, paused = await middleware.process_chat_payload(
        request,
        {'model': 'oi:leaf', 'messages': [saved, {'role': 'user', 'content': 'Next'}]},
        SimpleNamespace(id='u', role='admin'),
        {'chat_id': '', 'params': {}, 'filter_ids': ['modify']},
        model,
    )
    assert not paused
    if redact:
        assert form['messages'][0][field] == replacement
    _, payload = build_request(form, binding('ollama'), connection('ollama'))
    assert saved == original
    assert ('private-thought-marker' in json.dumps(payload)) is (not redact), payload
    if redact and replacement == '' and field != 'thinking':
        assert payload['messages'][0].get('thinking') in (None, '')
    else:
        assert payload['messages'][0].get('thinking') == (replacement if redact else 'private-thought-marker')


@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('continued', [False, True])
@pytest.mark.parametrize('sidecar', [False, True])
@pytest.mark.parametrize('resolved_alias', [False, True])
def test_legacy_tool_alias_retains_the_original_provider_function(protocol, continued, sidecar, resolved_alias):
    from open_webui.utils.tool_search import CALL_TOOL_NAME, resolve_tool_call

    provider = {'name': CALL_TOOL_NAME, 'arguments': json.dumps({'tool_name': 'lookup', 'params': '{"q":"x"}'})}
    call = {'type': 'function_call', 'call_id': 't1', 'status': 'completed', **provider}
    if resolved_alias:
        live = {'id': 't1', 'function': copy.deepcopy(provider)}
        assert resolve_tool_call(live, {CALL_TOOL_NAME: {'tool_id': f'builtin:{CALL_TOOL_NAME}'}, 'lookup': {}}) is None
        call.update(**live['function'], provider_function=live['provider_function'])
    tool_part = {
        'functionCall': {'name': provider['name'], 'args': json.loads(provider['arguments'])},
        'thoughtSignature': 'retained-signature',
    }
    bodies = [
        {'candidates': [{'content': {'role': 'model', 'parts': [tool_part]}, 'finishReason': 'STOP'}]},
        {'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'answer'}]}, 'finishReason': 'STOP'}]},
    ]
    calls = [
        {'call_id': f'call-{i}', 'binding_id': 'oi:leaf', 'protocol': protocol, 'body': body, 'replayable': True}
        for i, body in enumerate(bodies)
    ]
    if sidecar:
        calls[0]['generated_tool_ids'] = {'0': 't1'}
    if continued:
        calls.append(
            {
                'call_id': 'call-2',
                'binding_id': 'oi:leaf',
                'protocol': protocol,
                'replayable': True,
                'body': {
                    'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'more'}]}, 'finishReason': 'STOP'}]
                },
            }
        )
    saved = {
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'answer\nmore' if continued else 'answer',
        'oi_native': calls,
        'output': [
            call,
            {'type': 'function_call_output', 'call_id': 't1', 'output': [{'type': 'input_text', 'text': 'result'}]},
            output_text('answer\nmore' if continued else 'answer'),
        ],
    }
    original = copy.deepcopy(saved)
    payload, error = None, None
    try:
        messages = sanitize_tool_pairs(process_messages_with_output([saved]))
        _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert saved == original
    assert error is None, error
    parts = [part for item in payload['contents'] for part in item['parts']]
    assert sum('functionCall' in p for p in parts) == 1, payload
    assert sum('functionResponse' in p for p in parts) == 1
    assert next(p for p in parts if 'functionCall' in p) == tool_part


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('tool_count', [1, 2])
@pytest.mark.parametrize('provider_ids', [False, True])
@pytest.mark.parametrize('continued', [False, True])
async def test_ollama_tool_history_can_replay_after_continue(
    monkeypatch, tool_count, provider_ids, continued, stream, legacy
):
    import httpx
    from open_webui.models.chats import Chats
    from open_webui.utils.oi.protocols import generate
    from open_webui.utils.oi.runtime import SSEDecoder

    tools = [
        {'function': {'name': 'lookup', 'arguments': {'q': str(i)}}, **({'id': f't{i}'} if provider_ids else {})}
        for i in range(tool_count)
    ]
    body = {
        'model': 'synthetic-model',
        'message': {'role': 'assistant', 'content': 'prefix', 'tool_calls': tools},
        'done': True,
    }
    metadata = {'chat_id': 'saved', 'message_id': 'reply'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    saved_calls = AsyncMock()
    monkeypatch.setattr(Chats, 'get_message_by_id_and_message_id', AsyncMock(return_value={}))
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', saved_calls)
    response = httpx.Response(200, content=json.dumps(body) + '\n') if stream else httpx.Response(200, json=body)
    result = await generate(
        request,
        {'messages': [], 'metadata': metadata, 'stream': stream},
        SimpleNamespace(id='u'),
        binding('ollama'),
        connection('ollama'),
        'call-0',
        httpx.MockTransport(lambda _: response),
    )
    if stream:
        decoder, events = SSEDecoder(), []
        async for chunk in result.body_iterator:
            events.extend(decoder.feed(chunk.encode() if isinstance(chunk, str) else chunk))
        first = {
            'tool_calls': [
                tool
                for event in events
                for choice in event.get('choices', [])
                for tool in choice.get('delta', {}).get('tool_calls', [])
            ]
        }
    else:
        first = result['choices'][0]['message']
    assert len(first['tool_calls']) == tool_count
    ids = [tool['id'] for tool in first['tool_calls']]
    calls = [
        copy.deepcopy(saved_calls.call_args.args[2]['oi_native'][0]),
        {
            'call_id': 'call-1',
            'protocol': 'ollama',
            'binding_id': 'oi:leaf',
            'replayable': True,
            'body': {'message': {'role': 'assistant', 'content': 'answer'}, 'done': True},
        },
    ]
    assert calls[0]['body']['message']['tool_calls'] == tools
    if not provider_ids:
        assert set(calls[0]['generated_tool_ids'].values()) == set(ids)
    if legacy:
        calls[0].pop('generated_tool_ids', None)
    if continued:
        calls.append(
            {
                'call_id': 'call-2',
                'protocol': 'ollama',
                'binding_id': 'oi:leaf',
                'replayable': True,
                'body': {'message': {'role': 'assistant', 'content': 'more'}, 'done': True},
            }
        )
    saved = {
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'prefixanswer' + ('more' if continued else ''),
        'oi_native': calls,
        'output': [
            output_text('prefix'),
            *[
                {'type': 'function_call', 'call_id': tool['id'], 'status': 'completed', **tool['function']}
                for tool in first['tool_calls']
            ],
            *[
                {
                    'type': 'function_call_output',
                    'call_id': call_id,
                    'output': [{'type': 'input_text', 'text': 'result'}],
                }
                for call_id in ids
            ],
            output_text('answer\nmore' if continued else 'answer'),
        ],
    }
    error, payload = None, None
    try:
        messages = process_messages_with_output([saved])
        _, payload = build_request({'messages': messages}, binding('ollama'), connection('ollama'))
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert error is None, {
        'tool_count': tool_count,
        'provider_ids': provider_ids,
        'continued': continued,
        'stream': stream,
        'error': error,
    }
    assert sum(len(m.get('tool_calls', [])) for m in payload['messages']) == tool_count


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('tool_count', [1, 2])
async def test_temporary_ollama_tool_ids_roundtrip_without_transport_indexes(monkeypatch, stream, tool_count):
    import httpx
    from open_webui.utils.oi.protocols import generate
    from open_webui.utils.oi.runtime import SSEDecoder
    from open_webui.utils.oi.temporary_native import restore
    from open_webui.models.chats import Chats

    tools = [
        {'function': {'index': 4 + 3 * i, 'name': 'lookup', 'arguments': {'q': str(i)}}} for i in range(tool_count)
    ]
    body = {
        'model': 'fixture',
        'message': {'role': 'assistant', 'content': 'prefix', 'tool_calls': tools},
        'done': True,
    }
    metadata = {'chat_id': 'local:socket', 'message_id': 'reply', 'oi_temporary_id': 'temporary'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    saved = AsyncMock()
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', saved)
    frames = [
        {
            'model': 'fixture',
            'message': {'role': 'assistant', 'content': 'prefix' if i == 0 else '', 'tool_calls': [tool]},
            'done': False,
        }
        for i, tool in enumerate(tools)
    ]
    frames.append({'model': 'fixture', 'message': {'role': 'assistant', 'content': ''}, 'done': True})
    response = (
        httpx.Response(200, content=''.join(json.dumps(frame) + '\n' for frame in frames))
        if stream
        else httpx.Response(200, json=body)
    )
    result = await generate(
        request,
        {'messages': [], 'metadata': metadata, 'stream': stream},
        SimpleNamespace(id='u'),
        binding('ollama'),
        connection('ollama'),
        'call-0',
        httpx.MockTransport(lambda _: response),
    )
    if stream:
        decoder, events = SSEDecoder(), []
        async for part in result.body_iterator:
            events.extend(decoder.feed(part.encode() if isinstance(part, str) else part))
        canonical_tools = [
            t for e in events for c in e.get('choices', []) for t in c.get('delta', {}).get('tool_calls', [])
        ]
        capsule = next(e['oi_native_state'] for e in events if e.get('oi_native_state'))
        message = {'role': 'assistant', 'content': 'prefix', 'tool_calls': canonical_tools}
    else:
        message = result['choices'][0]['message']
        capsule = result['oi_native_state']
    saved.assert_not_called()
    message = copy.deepcopy(message)
    for tool in message['tool_calls']:
        tool.pop('index', None)  # Stream indexes route deltas; they are not assistant call content.
    message.update(id='reply', oi_native_state=capsule)
    calls = restore(message, 'u', 'temporary')
    assert calls and not message.get('oi_native_invalidated')
    assert list(calls[0]['generated_tool_ids'].values()) == [t['id'] for t in message['tool_calls']]
    assert calls[0]['body']['message']['tool_calls'] == tools
    from open_webui.utils.oi.replay import replay_message

    replayed = replay_message(calls[0])
    assert [t['id'] for t in replayed['tool_calls']] == [t['id'] for t in message['tool_calls']]


@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy', 'ollama'])
@pytest.mark.parametrize('change', ['name', 'arguments'])
def test_legacy_replay_rejects_changed_provider_function(protocol, change):
    if protocol == 'ollama':
        body = {
            'message': {
                'role': 'assistant',
                'content': 'prefix',
                'tool_calls': [{'function': {'name': 'lookup', 'arguments': {'q': 'original'}}}],
            },
            'done': True,
        }
    else:
        body = {
            'candidates': [
                {
                    'content': {
                        'role': 'model',
                        'parts': [{'text': 'prefix'}, {'functionCall': {'name': 'lookup', 'args': {'q': 'original'}}}],
                    },
                    'finishReason': 'STOP',
                }
            ]
        }
    call = {
        'type': 'function_call',
        'call_id': 't1',
        'name': 'lookup',
        'arguments': '{"q":"original"}',
        'status': 'completed',
    }
    call[change] = 'different' if change == 'name' else '{"q":"changed"}'
    saved = {
        'role': 'assistant',
        'content': 'prefix',
        'oi_native': [{'protocol': protocol, 'binding_id': 'oi:leaf', 'body': body}],
        'output': [
            output_text('prefix'),
            call,
            {'type': 'function_call_output', 'call_id': 't1', 'output': [{'type': 'input_text', 'text': 'result'}]},
        ],
    }
    with pytest.raises(ValueError, match='Native tool does not match'):
        process_messages_with_output([saved])


@pytest.mark.parametrize('edited', [False, True])
def test_legacy_indexed_temporary_capsule_preserves_history_but_rejects_edits(edited):
    from open_webui.utils.oi.temporary_native import cipher, digest, restore

    native = {
        'protocol': 'ollama',
        'binding_id': 'oi:leaf',
        'body': {
            'message': {
                'role': 'assistant',
                'content': 'prefix',
                'tool_calls': [{'function': {'name': 'lookup', 'arguments': {}}}],
            },
            'done': True,
        },
    }
    value = {
        'role': 'assistant',
        'content': 'prefix',
        'tool_calls': [
            {'index': 0, 'id': 'legacy-id', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}
        ],
    }
    # Authenticated v1 shape from before transport-index normalization.
    capsule = (
        cipher()
        .encrypt(
            json.dumps(
                {'user': 'u', 'temporary': 'temp', 'message': 'reply', 'projection': digest(value), 'calls': [native]}
            ).encode()
        )
        .decode()
    )
    message = {**copy.deepcopy(value), 'id': 'reply', 'oi_native_state': capsule}
    if edited:
        message['tool_calls'][0]['function']['arguments'] = '{"changed":true}'
    calls = restore(message, 'u', 'temp')
    if edited:
        assert calls == [] and message['oi_native_invalidated']
    else:
        assert calls
        # Legacy API messages have tool_calls rather than an output-item list.
        normalized = process_messages_with_output(
            [message, {'role': 'tool', 'tool_call_id': 'legacy-id', 'content': 'result'}]
        )
        _, payload = build_request({'messages': normalized}, binding('ollama'), connection('ollama'))
        assert len(payload['messages'][0]['tool_calls']) == 1


@pytest.mark.parametrize('protocol', ['gemini', 'ollama'])
def test_history_decoder_never_invents_an_id_without_a_saved_mapping(protocol):
    from open_webui.utils.oi.replay import replay_message

    body = (
        {
            'message': {
                'role': 'assistant',
                'content': 'prefix',
                'tool_calls': [{'function': {'name': 'lookup', 'arguments': {}}}],
            },
            'done': True,
        }
        if protocol == 'ollama'
        else {
            'candidates': [
                {
                    'content': {'role': 'model', 'parts': [{'functionCall': {'name': 'lookup', 'args': {}}}]},
                    'finishReason': 'STOP',
                }
            ]
        }
    )
    with pytest.raises(ValueError, match='Native tool does not match'):
        replay_message({'protocol': protocol, 'binding_id': 'oi:leaf', 'body': body})


@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy', 'ollama'])
@pytest.mark.parametrize('sidecar', [False, True])
def test_cancelled_dispatched_tool_stays_cancelled_after_continue(protocol, sidecar):
    from open_webui.utils.tool_search import CALL_TOOL_NAME, resolve_tool_call

    provider = {'name': CALL_TOOL_NAME, 'arguments': json.dumps({'tool_name': 'lookup', 'params': '{}'})}
    live = {'id': 't1', 'function': copy.deepcopy(provider)}
    assert resolve_tool_call(live, {CALL_TOOL_NAME: {'tool_id': f'builtin:{CALL_TOOL_NAME}'}, 'lookup': {}}) is None
    if protocol == 'ollama':
        bodies = [
            {
                'message': {
                    'role': 'assistant',
                    'content': 'prefix',
                    'tool_calls': [
                        {'function': {'name': provider['name'], 'arguments': json.loads(provider['arguments'])}}
                    ],
                },
                'done': True,
            },
            {'message': {'role': 'assistant', 'content': 'more'}, 'done': True},
        ]
        position = '0'
    else:
        bodies = [
            {
                'candidates': [
                    {
                        'content': {
                            'role': 'model',
                            'parts': [
                                {'text': 'prefix'},
                                {
                                    'functionCall': {
                                        'name': provider['name'],
                                        'args': json.loads(provider['arguments']),
                                    },
                                    'thoughtSignature': 'cancelled-signature',
                                },
                            ],
                        },
                        'finishReason': 'STOP',
                    }
                ]
            },
            {'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'more'}]}, 'finishReason': 'STOP'}]},
        ]
        position = '1'
    calls = [
        {'call_id': str(i), 'protocol': protocol, 'binding_id': 'oi:leaf', 'body': body, 'replayable': True}
        for i, body in enumerate(bodies)
    ]
    if sidecar:
        calls[0]['generated_tool_ids'] = {position: 't1'}
    saved = {
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'prefixmore',
        'oi_native': calls,
        'output': [
            output_text('prefix'),
            {
                'type': 'function_call',
                'call_id': 't1',
                'status': 'incomplete',
                **live['function'],
                'provider_function': live['provider_function'],
            },
            output_text('more'),
        ],
    }
    original = copy.deepcopy(saved)
    messages = sanitize_tool_pairs(process_messages_with_output([saved]))
    _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    if protocol == 'ollama':
        assert sum(len(m.get('tool_calls') or []) for m in payload['messages']) == 0
        assert not any(m['role'] == 'tool' for m in payload['messages'])
    else:
        assert 'functionCall' not in json.dumps(payload) and 'functionResponse' not in json.dumps(payload)
    assert 'prefix' in json.dumps(payload) and 'more' in json.dumps(payload)
    assert saved == original
