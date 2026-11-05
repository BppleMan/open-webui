"""Call-boundary and consumer-ID replay regressions; synthetic suppliers/tools only."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.models.chats import Chats
from open_webui.utils import middleware
from open_webui.utils.middleware import process_messages_with_output, sanitize_tool_pairs
from open_webui.utils.oi.protocols import build_request, generate
from open_webui.utils.oi.runtime import SSEDecoder

from test.oi.test_review_regressions import binding, connection


def text_item(value):
    return {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': value}]}


def tool_items(tool):
    return [
        {'type': 'function_call', 'call_id': tool['id'], 'status': 'completed', **tool['function']},
        {
            'type': 'function_call_output',
            'call_id': tool['id'],
            'output': [{'type': 'input_text', 'text': 'synthetic-result'}],
        },
    ]


def gemini_body(text='', tool=None, finish='STOP'):
    parts = ([{'text': text}] if text else []) + (
        [{'functionCall': tool, 'thoughtSignature': 'fixture-signature'}] if tool else []
    )
    candidate = {'content': {'role': 'model', 'parts': parts}}
    if finish:
        candidate['finishReason'] = finish
    return {'candidates': [candidate]}


async def run_provider(monkeypatch, protocol, body, stream, call_id, previous=None):
    saved = copy.deepcopy(previous or {})
    writes = []

    async def capture(chat_id, message_id, update, **kwargs):
        saved.update(copy.deepcopy(update))
        writes.append(copy.deepcopy(update))

    monkeypatch.setattr(
        Chats, 'get_message_by_id_and_message_id', AsyncMock(side_effect=lambda *args: copy.deepcopy(saved))
    )
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', capture)
    metadata = {'chat_id': 'synthetic-chat', 'message_id': 'reply'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    response = (
        httpx.Response(200, content='data: ' + json.dumps(body) + '\n\n') if stream else httpx.Response(200, json=body)
    )
    result = await generate(
        request,
        {'messages': [], 'metadata': metadata, 'stream': stream},
        SimpleNamespace(id='u'),
        binding(protocol),
        connection(protocol),
        call_id,
        httpx.MockTransport(lambda _: response),
    )
    if stream:
        decoder, events = SSEDecoder(), []
        async for chunk in result.body_iterator:
            events.extend(decoder.feed(chunk.encode() if isinstance(chunk, str) else chunk))
        tools = [t for e in events for c in e.get('choices', []) for t in c.get('delta', {}).get('tool_calls', [])]
        content = ''.join(c.get('delta', {}).get('content', '') for e in events for c in e.get('choices', []))
        message = {'role': 'assistant', 'content': content, 'tool_calls': tools}
    else:
        message = result['choices'][0]['message']
    assert writes
    return message, saved


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('stream,finish', [(False, 'MAX_TOKENS'), (True, 'MAX_TOKENS'), (True, None)])
@pytest.mark.parametrize('suppressed', [False, True])
async def test_suppressed_native_tool_does_not_consume_later_canonical_slot(
    monkeypatch, protocol, stream, finish, suppressed
):
    # A length-limited response retains raw provider blocks, but deliberately
    # does not dispatch its function call. Continue later produces a valid tool.
    first, saved = await run_provider(
        monkeypatch,
        protocol,
        gemini_body('prefix', {'name': 'discarded', 'args': {}} if suppressed else None, finish),
        stream,
        'first',
    )
    assert not first.get('tool_calls')
    assert saved['oi_native'][0]['replayable'] is bool(finish)
    second, saved = await run_provider(
        monkeypatch,
        protocol,
        gemini_body(tool={'id': 't1', 'name': 'lookup', 'args': {'q': 'ok'}}),
        stream,
        'second',
        saved,
    )
    final, saved = await run_provider(monkeypatch, protocol, gemini_body('answer'), stream, 'third', saved)
    assert len(second['tool_calls']) == 1
    saved.update(
        role='assistant',
        model='oi:leaf',
        content='prefixanswer',
        output=[text_item(first['content']), *tool_items(second['tool_calls'][0]), text_item(final['content'])],
    )
    original = copy.deepcopy(saved)
    messages = sanitize_tool_pairs(process_messages_with_output([saved]))
    _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    assert saved == original
    encoded = json.dumps(payload)
    assert 'prefix' in encoded and 'answer' in encoded
    assert 'discarded' not in encoded
    assert sum('functionCall' in p for c in payload['contents'] for p in c['parts']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('legacy_prefix', [False, True])
async def test_native_suffix_matches_tools_after_legacy_history(monkeypatch, protocol, legacy_prefix):
    # Existing upstream chats have canonical output but no oi_native. A Continue
    # saves only newly generated native calls on the same assistant message.
    old = {'id': 'old-tool', 'function': {'name': 'old_lookup', 'arguments': '{}'}}
    first, saved = await run_provider(
        monkeypatch,
        protocol,
        gemini_body(tool={'id': 'new-tool', 'name': 'new_lookup', 'args': {}}),
        False,
        'new-first',
    )
    final, saved = await run_provider(monkeypatch, protocol, gemini_body('new answer'), False, 'new-last', saved)
    prefix = [*tool_items(old), text_item('old answer')] if legacy_prefix else []
    saved.update(
        role='assistant',
        model='oi:leaf',
        output=[*prefix, *tool_items(first['tool_calls'][0]), text_item(final['content'])],
    )
    original = copy.deepcopy(saved)
    messages = sanitize_tool_pairs(process_messages_with_output([saved]))
    _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    assert saved == original
    encoded = json.dumps(payload)
    assert 'new_lookup' in encoded
    assert 'fixture-signature' in encoded
    if legacy_prefix:
        assert 'old_lookup' in encoded and 'old answer' in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('reuse_provider_id', [False, True])
async def test_consumer_remapped_tool_ids_remain_replayable(monkeypatch, protocol, reuse_provider_id):
    first, saved = await run_provider(
        monkeypatch,
        protocol,
        gemini_body('prefix', tool={'id': 't1', 'name': 'lookup', 'args': {'q': 'first'}}),
        True,
        'call-first',
    )
    saved.update(role='assistant', model='oi:leaf', output=[text_item('prefix'), *tool_items(first['tool_calls'][0])])
    writes, events, payloads, tool_inputs = [], [], [], []

    async def capture(chat_id, message_id, update, **kwargs):
        saved.update(copy.deepcopy(update))
        writes.append(copy.deepcopy(update))

    async def emit(event):
        events.append(copy.deepcopy(event))

    monkeypatch.setattr(
        Chats, 'get_message_by_id_and_message_id', AsyncMock(side_effect=lambda *args: copy.deepcopy(saved))
    )
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', capture)
    monkeypatch.setattr(Chats, 'get_chat_title_by_id', AsyncMock(return_value='synthetic'))
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', False)
    monkeypatch.setattr(middleware, 'log_model_usage', lambda *args: None)
    monkeypatch.setattr(middleware, 'publish_chat_finished_event', AsyncMock())
    monkeypatch.setattr(middleware, 'outlet_filter_handler', AsyncMock())
    monkeypatch.setattr(middleware, 'background_tasks_handler', AsyncMock())
    monkeypatch.setattr(middleware.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware.Config, 'get_many', AsyncMock(return_value={}))

    async def lookup(q: str):
        tool_inputs.append(q)
        return 'synthetic-result:' + q

    metadata = {
        'chat_id': 'synthetic-chat',
        'message_id': 'reply',
        'assistant_message_id': 'reply',
        'params': {},
        'tools': {
            'lookup': {
                'callable': lookup,
                'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
            }
        },
    }
    model = {
        'id': 'oi:leaf',
        'owned_by': 'openai',
        'oi_binding': True,
        'connection': {'protocol': protocol, 'transport': 'server'},
    }
    request = SimpleNamespace(
        state=SimpleNamespace(metadata=metadata, max_tool_call_iterations=3),
        app=SimpleNamespace(state=SimpleNamespace(redis=None, MODELS={'oi:leaf': model})),
    )
    user = SimpleNamespace(id='u', role='admin')
    form = {
        'model': 'oi:leaf',
        'messages': [{'role': 'user', 'content': 'Continue'}],
        'metadata': metadata,
        'stream': True,
    }
    raw_id = 't1' if reuse_provider_id else 't2'
    body = gemini_body(tool={'id': raw_id, 'name': 'lookup', 'args': {'q': 'second'}})
    response = await generate(
        request,
        form,
        user,
        binding(protocol),
        connection(protocol),
        'call-second',
        httpx.MockTransport(lambda _: httpx.Response(200, content='data: ' + json.dumps(body) + '\n\n')),
    )

    async def followup(request, followup_form, user, **kwargs):
        def provider(req):
            payloads.append(json.loads(req.content))
            return httpx.Response(200, content='data: ' + json.dumps(gemini_body('answer')) + '\n\n')

        return await generate(
            request,
            followup_form,
            user,
            binding(protocol),
            connection(protocol),
            'call-last',
            httpx.MockTransport(provider),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', followup)
    ctx = {
        'request': request,
        'form_data': form,
        'user': user,
        'model': model,
        'metadata': metadata,
        'events': [],
        'event_emitter': emit,
        'event_caller': None,
        'assistant_message': copy.deepcopy(saved),
        'tasks': {},
    }
    await middleware.process_chat_response(response, ctx)
    output = saved['output']
    calls = [item for item in output if item['type'] == 'function_call']
    assert len(calls) == 2, {'output': output, 'events': events}
    assert (calls[-1]['call_id'] != raw_id) is reuse_provider_id
    assert calls[-1]['status'] == 'completed'
    assert tool_inputs == ['second']
    assert len(payloads) == 1
    followup_functions = [p for c in payloads[0]['contents'] for p in c['parts'] if 'functionCall' in p]
    followup_results = [p for c in payloads[0]['contents'] for p in c['parts'] if 'functionResponse' in p]
    assert len(followup_results) == 1
    # Save the secondary downstream effect before checking immediate request fidelity.
    error = None
    original = copy.deepcopy(saved)
    try:
        normalized = sanitize_tool_pairs(process_messages_with_output([saved]))
        _, payload = build_request({'messages': normalized}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert saved == original
    assert len(followup_functions) == 1 and error is None, {'followup_payload': payloads[0], 'next_turn_error': error}
    assert sum('functionCall' in p for c in payload['contents'] for p in c['parts']) == 2
    assert followup_functions[0] == body['candidates'][0]['content']['parts'][0]
    assert followup_results[0]['functionResponse']['id'] == raw_id
    assert not request.state.oi_native_streams
    assert saved['oi_native'][1]['body'] == body
    assert list(saved['oi_native'][1]['generated_tool_ids'].values()) == [calls[-1]['call_id']]
    from open_webui.utils.oi.native_privacy import replay_envelope

    portable = copy.deepcopy(saved)
    portable['oi_native'] = [replay_envelope(n) for n in portable['oi_native']]
    _, exported_payload = build_request(
        {'messages': sanitize_tool_pairs(process_messages_with_output([portable]))},
        binding(protocol),
        connection(protocol),
    )
    assert exported_payload == payload


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('parallel', [False, True])
async def test_remapped_temporary_stream_capsule_keeps_position_and_raw_ids(monkeypatch, protocol, parallel):
    from open_webui.utils.oi.replay import remap_stream_tool
    from open_webui.utils.oi.temporary_native import restore

    persist = AsyncMock()
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', persist)
    body = gemini_body('prefix', {'id': 'reused', 'name': 'lookup', 'args': {'q': 'one'}})
    if parallel:
        body['candidates'][0]['content']['parts'].append(
            {
                'functionCall': {'id': 'reused', 'name': 'lookup', 'args': {'q': 'two'}},
                'thoughtSignature': 'second-signature',
            }
        )
    metadata = {'chat_id': '', 'message_id': 'reply', 'oi_temporary_id': 'temporary'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    response = await generate(
        request,
        {'messages': [], 'metadata': metadata, 'stream': True},
        SimpleNamespace(id='u'),
        binding(protocol),
        connection(protocol),
        'native-call',
        httpx.MockTransport(lambda _: httpx.Response(200, content='data: ' + json.dumps(body) + '\n\n')),
    )
    decoder, tools, capsule = SSEDecoder(), [], None
    async for chunk in response.body_iterator:
        for event in decoder.feed(chunk.encode() if isinstance(chunk, str) else chunk):
            capsule = event.get('oi_native_state') or capsule
            for choice in event.get('choices', []):
                for tool in choice.get('delta', {}).get('tool_calls', []):
                    original_id = tool['id']
                    tool['id'] = 'fc_canonical_' + str(tool['index'])
                    remap_stream_tool(request, response, tool['index'], original_id, tool['id'])
                    tools.append(tool)
    persist.assert_not_awaited()
    message = {
        'id': 'reply',
        'role': 'assistant',
        'content': 'prefix',
        'tool_calls': [{k: v for k, v in t.items() if k != 'index'} for t in tools],
        'oi_native_state': capsule,
    }
    native = restore(message, 'u', 'temporary')[0]
    assert native['body'] == body
    assert native['generated_tool_ids'] == {str(i + 1): t['id'] for i, t in enumerate(tools)}
    assert not request.state.oi_native_streams
    messages = process_messages_with_output([message])
    messages.extend({'role': 'tool', 'tool_call_id': t['id'], 'content': 'synthetic-result'} for t in tools)
    _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    assert payload['contents'][0]['parts'] == body['candidates'][0]['content']['parts']
    assert [c['parts'][0]['functionResponse']['id'] for c in payload['contents'][1:]] == ['reused'] * len(tools)


@pytest.mark.parametrize('legacy_prefix', [False, True])
@pytest.mark.parametrize('reasoning_only', [False, True])
def test_continued_text_keeps_legacy_prefix_and_reasoning_call_boundaries(legacy_prefix, reasoning_only):
    parts = [{'text': 'summary', 'thought': True, 'thoughtSignature': 'summary-signature'}]

    def native(key, body):
        return {'call_id': key, 'protocol': 'gemini', 'binding_id': 'oi:leaf', 'body': body}

    calls = [native('last', gemini_body('new answer'))]
    if reasoning_only:
        body = gemini_body()
        body['candidates'][0]['content']['parts'] = parts
        calls.insert(0, native('reasoning-only', body))
    saved = {
        'role': 'assistant',
        'oi_native': calls,
        'output': [text_item(('old answer\n' if legacy_prefix else '') + 'new answer')],
    }
    before = copy.deepcopy(saved)
    normalized = process_messages_with_output([saved])
    _, payload = build_request({'messages': normalized}, binding('gemini'), connection('gemini'))
    text = json.dumps(payload)
    assert ('old answer' in text) is legacy_prefix
    assert ('summary-signature' in text) is reasoning_only
    assert 'new answer' in text
    assert saved == before


@pytest.mark.parametrize('field,value', [('name', 'changed'), ('arguments', '{"q":"changed"}')])
def test_remapped_sidecar_does_not_accept_changed_canonical_functions(field, value):
    body = gemini_body(tool={'id': 'reused', 'name': 'lookup', 'args': {'q': 'original'}})
    saved = {
        'role': 'assistant',
        'oi_native': [
            {
                'call_id': 'new-call',
                'protocol': 'gemini',
                'binding_id': 'oi:leaf',
                'body': body,
                'generated_tool_ids': {'0': 'fc_renumbered'},
            }
        ],
        'output': tool_items({'id': 'fc_renumbered', 'function': {'name': 'lookup', 'arguments': '{"q":"original"}'}}),
    }
    saved['output'][0][field] = value
    with pytest.raises(ValueError, match='Native tool does not match'):
        process_messages_with_output([saved])


@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('continued', [False, True])
def test_cancelled_remapped_parallel_call_does_not_reappear(protocol, continued):
    body = gemini_body('prefix', {'id': 'reused', 'name': 'lookup', 'args': {'q': 'keep'}})
    body['candidates'][0]['content']['parts'].append(
        {
            'functionCall': {'id': 'reused', 'name': 'lookup', 'args': {'q': 'cancel'}},
            'thoughtSignature': 'cancelled-signature',
        }
    )
    keep = {'id': 'fc_keep', 'function': {'name': 'lookup', 'arguments': '{"q":"keep"}'}}
    cancel = {
        'type': 'function_call',
        'call_id': 'fc_cancel',
        'status': 'incomplete',
        'name': 'lookup',
        'arguments': '{"q":"cancel"}',
    }
    paired = tool_items(keep)
    saved = {
        'role': 'assistant',
        'oi_native': [
            {
                'call_id': 'tool-call',
                'binding_id': 'oi:leaf',
                'protocol': protocol,
                'body': body,
                'generated_tool_ids': {'1': 'fc_keep', '2': 'fc_cancel'},
            }
        ],
        'output': [text_item('prefix'), paired[0], cancel, paired[1]],
    }
    if continued:
        saved['oi_native'].append(
            {'call_id': 'last', 'binding_id': 'oi:leaf', 'protocol': protocol, 'body': gemini_body('answer')}
        )
        saved['output'].append(text_item('answer'))
    original = copy.deepcopy(saved)
    _, payload = build_request(
        {'messages': sanitize_tool_pairs(process_messages_with_output([saved]))},
        binding(protocol),
        connection(protocol),
    )
    tools = [p for c in payload['contents'] for p in c['parts'] if 'functionCall' in p]
    results = [p['functionResponse'] for c in payload['contents'] for p in c['parts'] if 'functionResponse' in p]
    assert tools == [body['candidates'][0]['content']['parts'][1]]
    assert len(results) == 1 and results[0]['id'] == 'reused'
    assert 'cancelled-signature' not in json.dumps(payload)
    assert saved == original
