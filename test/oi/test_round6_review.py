"""Request filtering, cancelled tools and visible media regressions. Synthetic fixtures only."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.utils import filter as filters
from open_webui.utils.middleware import process_messages_with_output, sanitize_tool_pairs
from open_webui.utils.oi.protocols import build_request
from test.oi.test_review_regressions import binding, connection


def envelope(protocol, text, tool=False):
    if protocol == 'anthropic':
        body = {
            'content': [{'type': 'text', 'text': text}] if text else [],
            'stop_reason': 'tool_use' if tool else 'end_turn',
        }
        if tool:
            body['content'].append({'type': 'tool_use', 'id': 't1', 'name': 'lookup', 'input': {}})
    elif protocol == 'openai-responses':
        body = {
            'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}],
            'status': 'completed',
        }
    else:
        parts = [{'text': text}] if text else []
        if tool:
            parts.append({'functionCall': {'id': 't1', 'name': 'lookup', 'args': {}}})
        body = {'candidates': [{'content': {'role': 'model', 'parts': parts}, 'finishReason': 'STOP'}]}
    return {'call_id': 'call-1', 'binding_id': 'oi:leaf', 'protocol': protocol, 'body': body, 'replayable': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['anthropic', 'gemini', 'openai-responses'])
@pytest.mark.parametrize('stage', ['inlet', 'request'])
@pytest.mark.parametrize('native', [False, True])
async def test_request_filters_change_the_actual_native_history(monkeypatch, protocol, stage, native):
    async def redact(body):
        body = copy.deepcopy(body)
        body['messages'][0]['content'] = 'REDACTED'
        return body

    monkeypatch.setattr(filters, 'get_function_module', AsyncMock(return_value=SimpleNamespace(**{stage: redact})))
    monkeypatch.setattr(filters, 'ENABLE_FUNCTIONS', True)
    saved = {
        'id': 'reply',
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'private-original-text',
        'output': [
            {
                'type': 'message',
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': 'private-original-text'}],
            }
        ],
    }
    if native:
        saved['oi_native'] = [envelope(protocol, 'private-original-text')]
    form = {'messages': process_messages_with_output([saved, {'role': 'user', 'content': 'Continue'}])}
    filtered, _ = await filters.process_filter_functions(
        request=SimpleNamespace(state=SimpleNamespace()),
        filter_functions=[SimpleNamespace(id='redact')],
        filter_type=stage,
        form_data=form,
        extra_params={},
        filter_context=None,
    )
    assert filtered['messages'][0]['content'] == 'REDACTED'
    _, payload = build_request(filtered, binding(protocol), connection(protocol))
    encoded = json.dumps(payload)
    assert 'private-original-text' not in encoded and 'REDACTED' in encoded, payload


@pytest.mark.parametrize('protocol', ['anthropic', 'gemini'])
@pytest.mark.parametrize('caption', [False, True])
@pytest.mark.parametrize('paired', [False, True])
def test_cancelled_tool_is_not_reintroduced_by_native_replay(protocol, caption, paired):
    text = 'Let me check.' if caption else ''
    output = [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}]
    output.append(
        {
            'type': 'function_call',
            'call_id': 't1',
            'name': 'lookup',
            'arguments': '{}',
            'status': 'completed' if paired else 'incomplete',
        }
    )
    if paired:
        output.append(
            {'type': 'function_call_output', 'call_id': 't1', 'output': [{'type': 'input_text', 'text': 'result'}]}
        )
    saved = {
        'id': 'reply',
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': text,
        'output': output,
        'oi_native': [envelope(protocol, text, tool=True)],
    }
    messages = sanitize_tool_pairs(process_messages_with_output([saved, {'role': 'user', 'content': 'Next'}]))
    if not paired:
        assert not any(message.get('tool_calls') for message in messages)
    _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    encoded = json.dumps(payload)
    call_marker = 'tool_use' if protocol == 'anthropic' else 'functionCall'
    result_marker = 'tool_result' if protocol == 'anthropic' else 'functionResponse'
    assert (call_marker in encoded) == paired, {'canonical': messages, 'payload': payload}
    if paired:
        assert result_marker in encoded


@pytest.mark.parametrize('caption', [False, True])
@pytest.mark.parametrize('change', ['same_binding', 'different_binding', 'edited_caption'])
def test_visible_generated_image_is_preserved_when_native_replay_is_unavailable(caption, change):
    text = 'A caption' if caption else ''
    native = envelope('gemini', text)
    native['body']['candidates'][0]['content']['parts'].insert(
        0, {'inlineData': {'mimeType': 'image/png', 'data': 'c3ludGhldGljLWltYWdl'}}
    )
    saved = {
        'id': 'reply',
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': text,
        'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}]
        if caption
        else [],
        'files': [{'type': 'image', 'url': 'data:image/png;base64,c3ludGhldGljLWltYWdl'}],
        'oi_native': [native],
    }
    target = binding('gemini')
    if change == 'different_binding':
        target.id = 'oi:other'
    if change == 'edited_caption':
        saved['content'] = 'Edited caption'
        saved['output'] = [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Edited caption'}]}
        ]
        saved['oi_native_invalidated'] = True
    messages = process_messages_with_output([saved, {'role': 'user', 'content': 'Describe the image'}])
    _, payload = build_request({'messages': messages}, target, connection('gemini'))
    assert 'c3ludGhldGljLWltYWdl' in json.dumps(payload), {'canonical': messages, 'payload': payload}


@pytest.mark.asyncio
@pytest.mark.parametrize('redact_history', [False, True])
@pytest.mark.parametrize('stage', ['pipeline_inlet', 'inlet', 'request'])
@pytest.mark.parametrize('native', [False, True])
async def test_full_payload_pipeline_honors_redacted_assistant_history(monkeypatch, stage, native, redact_history):
    from open_webui.utils import middleware

    async def redact(body):
        body = copy.deepcopy(body)
        if redact_history:
            body['messages'][0]['content'] = 'REDACTED'
        body['temperature'] = 0.2
        return body

    async def pipeline(request, body, user, models):
        return await redact(body) if stage == 'pipeline_inlet' else body

    monkeypatch.setattr(middleware.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_event_emitter', AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(middleware, 'get_event_call', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_filter_functions', AsyncMock(return_value=[SimpleNamespace(id='redact')]))
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', pipeline)
    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(filters, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(filters, 'get_function_module', AsyncMock(return_value=SimpleNamespace(**{stage: redact})))
    model = {
        'id': 'oi:leaf',
        'owned_by': 'openai',
        'oi_binding': True,
        'info': {'meta': {'capabilities': {'builtin_tools': False}}},
    }
    request = SimpleNamespace(
        state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={'oi:leaf': model}))
    )
    saved = {
        'id': 'reply',
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'private-original-text',
        'output': [
            {
                'type': 'message',
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': 'private-original-text'}],
            }
        ],
    }
    if native:
        saved['oi_native'] = [envelope('anthropic', 'private-original-text')]
        saved['oi_native'][0]['body']['content'].insert(
            0, {'type': 'thinking', 'thinking': 'Synthetic thought', 'signature': 'synthetic-signature'}
        )
    form, _, _, paused = await middleware.process_chat_payload(
        request,
        {'model': 'oi:leaf', 'messages': [saved, {'role': 'user', 'content': 'Continue'}]},
        SimpleNamespace(id='u', role='admin'),
        {'chat_id': '', 'params': {}, 'filter_ids': ['redact']},
        model,
    )
    assert not paused and form['messages'][0]['content'] == ('REDACTED' if redact_history else 'private-original-text')
    _, payload = build_request(form, binding('anthropic'), connection('anthropic'))
    assert ('private-original-text' in json.dumps(payload)) == (not redact_history), payload
    if native:
        assert ('synthetic-signature' in json.dumps(payload)) == (not redact_history)


@pytest.mark.asyncio
@pytest.mark.parametrize('edit', [False, True])
async def test_saved_image_reply_text_edit_keeps_image_in_followup(db, monkeypatch, edit):
    from contextlib import asynccontextmanager
    from open_webui.models.chats import Chat, Chats
    from open_webui.models.chat_messages import ChatMessages

    native = envelope('gemini', 'Original caption')
    native['body']['candidates'][0]['content']['parts'].insert(
        0, {'inlineData': {'mimeType': 'image/png', 'data': 'c3ludGhldGljLWltYWdl'}}
    )
    message = {
        'id': 'reply',
        'role': 'assistant',
        'model': 'oi:leaf',
        'user_id': 'u',
        'content': 'Original caption',
        'parentId': None,
        'childrenIds': [],
        'done': True,
        'timestamp': 1,
        'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Original caption'}]}
        ],
        'files': [{'type': 'image', 'url': 'data:image/png;base64,c3ludGhldGljLWltYWdl'}],
        'oi_native': [native],
    }
    row = Chat(
        id='image-chat',
        user_id='u',
        title='Image reply',
        chat={'history': {'currentId': 'reply', 'messages': {'reply': message}}},
        created_at=1,
        updated_at=1,
    )
    db.add(row)
    await db.flush()
    await ChatMessages.upsert_message('reply', row.id, 'u', message, db=db)

    @asynccontextmanager
    async def transaction(_):
        yield db, row
        await db.flush()

    monkeypatch.setattr(Chats, '_chat_transaction', transaction)
    if edit:
        await Chats.edit_reply_text(row.id, 'reply', 'Edited caption', 'u', {})
    saved = (await ChatMessages.get_messages_map_by_chat_id(row.id, db=db))['reply']
    assert saved['files'] == message['files']
    if edit:
        assert saved['oi_native_invalidated'] and saved['content'] == 'Edited caption'
    _, payload = build_request(
        {'messages': process_messages_with_output([saved, {'role': 'user', 'content': 'Describe the image'}])},
        binding('gemini'),
        connection('gemini'),
    )
    assert 'c3ludGhldGljLWltYWdl' in json.dumps(payload), payload


@pytest.mark.parametrize('protocol', ['anthropic', 'gemini', 'cpa-agy'])
@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('continued', [False, True])
def test_partial_cancellation_preserves_paired_tool_blocks_and_can_continue(protocol, partial, continued):
    native = envelope(protocol, 'Visible prefix', tool=True)
    if protocol == 'anthropic':
        tool = native['body']['content'][-1]
        blocks = native['body']['content']
        blocks.insert(0, {'type': 'thinking', 'thinking': 'Summary', 'signature': 'retained-signature'})
        cancelled = {**tool, 'id': 't2'}
    else:
        blocks = native['body']['candidates'][0]['content']['parts']
        tool = blocks[-1]
        tool['thoughtSignature'] = 'retained-signature'
        cancelled = {'functionCall': {**tool['functionCall'], 'id': 't2'}}
    blocks.append(cancelled)
    saved = {
        'id': 'reply',
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'Visible prefix',
        'oi_native': [native],
        'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Visible prefix'}]},
            *[
                {
                    'type': 'function_call',
                    'call_id': t,
                    'name': 'lookup',
                    'arguments': '{}',
                    'status': 'completed' if partial and t == 't1' else 'incomplete',
                }
                for t in ('t1', 't2')
            ],
        ],
    }
    if partial:
        saved['output'].append(
            {'type': 'function_call_output', 'call_id': 't1', 'output': [{'type': 'input_text', 'text': 'result'}]}
        )
    if continued:
        more = envelope(protocol, 'more')
        more['call_id'] = 'continued'
        saved['oi_native'].append(more)
        saved['output'].append(
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'more'}]}
        )
        saved['content'] += 'more'
    original = copy.deepcopy(saved)
    for _ in range(2):
        messages = sanitize_tool_pairs(process_messages_with_output([saved, {'role': 'user', 'content': 'Next'}]))
        _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
        encoded = json.dumps(payload)
        assert 't2' not in encoded and ('t1' in encoded) == partial
        assert 'Visible prefix' in encoded
        if partial:
            assert 'retained-signature' in encoded and 'result' in encoded
        if continued:
            assert 'more' in encoded
    assert saved == original


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['inlet', 'request'])
@pytest.mark.parametrize('change', ['sampling_only', 'tool_arguments', 'remove_image'])
async def test_filter_consistency_covers_tool_and_media_changes_without_mutating_source(monkeypatch, stage, change):
    from open_webui.utils.oi.protocols import NativeStream

    native = envelope('gemini', 'caption', tool=True)
    native['body']['candidates'][0]['content']['parts'].insert(
        0, {'inlineData': {'mimeType': 'image/png', 'data': 'cGlj'}}
    )
    message = NativeStream('gemini').nonstream(native['body'])['choices'][0]['message']
    message['oi_native'] = copy.deepcopy(native)
    message['content'] = [
        {'type': 'text', 'text': 'caption'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,cGlj'}},
    ]
    message.pop('images', None)
    source = copy.deepcopy(message)

    async def modify(body):
        body = copy.deepcopy(body)
        body['temperature'] = 0.3
        if change == 'tool_arguments':
            body['messages'][0]['tool_calls'][0]['function']['arguments'] = '{"new":true}'
        elif change == 'remove_image':
            body['messages'][0]['content'] = [{'type': 'text', 'text': 'caption'}]
        return body

    monkeypatch.setattr(filters, 'get_function_module', AsyncMock(return_value=SimpleNamespace(**{stage: modify})))
    monkeypatch.setattr(filters, 'ENABLE_FUNCTIONS', True)
    result, _ = await filters.process_filter_functions(
        SimpleNamespace(state=SimpleNamespace()),
        None,
        [SimpleNamespace(id='modify')],
        stage,
        {'messages': [message]},
        {},
    )
    assert message == source
    assert bool(result['messages'][0].get('oi_native')) == (change == 'sampling_only')
    _, payload = build_request(result, binding('gemini'), connection('gemini'))
    assert ('cGlj' in json.dumps(payload)) == (change != 'remove_image')
    if change == 'tool_arguments':
        assert '"new": true' in json.dumps(payload)


@pytest.mark.asyncio
async def test_explicit_nonvision_target_rejects_generated_image_history(monkeypatch):
    from fastapi import HTTPException
    from open_webui.utils import middleware

    monkeypatch.setattr(middleware.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_event_emitter', AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(middleware, 'get_event_call', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    model = {
        'id': 'oi:other',
        'oi_binding': True,
        'owned_by': 'openai',
        'info': {'meta': {'capabilities': {'vision': False}}},
    }
    request = SimpleNamespace(
        state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={'oi:other': model}))
    )
    image = {
        'role': 'assistant',
        'content': 'caption',
        'oi_native_invalidated': True,
        'files': [{'type': 'image', 'url': 'data:image/png;base64,cGlj'}],
    }
    with pytest.raises(HTTPException) as error:
        await middleware.process_chat_payload(
            request,
            {'model': 'oi:other', 'messages': [image, {'role': 'user', 'content': 'describe'}]},
            SimpleNamespace(id='u', role='admin'),
            {'chat_id': '', 'params': {}},
            model,
        )
    assert error.value.status_code == 422 and 'vision disabled' in error.value.detail


@pytest.mark.parametrize('paired', [False, True])
def test_native_only_api_tool_history_requires_actual_result(paired):
    native = envelope('gemini', '', tool=True)
    native['body']['candidates'][0]['content']['parts'][0]['thoughtSignature'] = 'retained'
    messages = [{'role': 'assistant', 'oi_native': native}]
    if paired:
        messages.append({'role': 'tool', 'tool_call_id': 't1', 'content': 'result'})
    messages.append({'role': 'user', 'content': 'Next'})
    _, payload = build_request({'messages': messages}, binding('gemini'), connection('gemini'))
    assert ('functionCall' in json.dumps(payload)) == paired


def test_outbound_pairing_still_applies_after_a_filter_removes_tool_results():
    native = envelope('gemini', 'Visible text', tool=True)
    messages = [
        {
            'role': 'assistant',
            'content': 'Visible text',
            'oi_native': native,
            'tool_calls': [{'id': 't1', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}],
        },
        {'role': 'user', 'content': 'Continue without that result'},
    ]
    _, payload = build_request({'messages': messages}, binding('gemini'), connection('gemini'))
    assert 'functionCall' not in json.dumps(payload)
    assert 'Visible text' in json.dumps(payload)


@pytest.mark.parametrize('cancelled', [None, 't1', 't2'])
def test_legacy_identical_parallel_gemini_calls_keep_order_when_one_is_cancelled(cancelled):
    native = envelope('gemini', 'prefix', tool=True)
    blocks = native['body']['candidates'][0]['content']['parts']
    blocks[-1]['functionCall'].pop('id')
    blocks.append(copy.deepcopy(blocks[-1]))
    # Both calls deliberately have identical names/arguments and no provider IDs.
    saved = {
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'prefixmore',
        'oi_native': [native, {**envelope('gemini', 'more'), 'call_id': 'continued'}],
        'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'prefix'}]},
            *[
                {
                    'type': 'function_call',
                    'call_id': t,
                    'name': 'lookup',
                    'arguments': '{}',
                    'status': 'incomplete' if t == cancelled else 'completed',
                }
                for t in ('t1', 't2')
            ],
            *[
                {
                    'type': 'function_call_output',
                    'call_id': t,
                    'output': [{'type': 'input_text', 'text': t + '-result'}],
                }
                for t in ('t1', 't2')
                if t != cancelled
            ],
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'more'}]},
        ],
    }
    original = copy.deepcopy(saved)
    messages = sanitize_tool_pairs(process_messages_with_output([saved]))
    calls = [call['id'] for message in messages for call in message.get('tool_calls', [])]
    assert calls == [t for t in ('t1', 't2') if t != cancelled]
    _, payload = build_request({'messages': messages}, binding('gemini'), connection('gemini'))
    parts = [part for content in payload['contents'] for part in content.get('parts', [])]
    assert sum('functionCall' in part for part in parts) == len(calls)
    assert sum('functionResponse' in part for part in parts) == len(calls)
    assert saved == original


@pytest.mark.parametrize(
    'protocol', ['anthropic', 'gemini', 'cpa-agy', 'gemini-interactions', 'openai-responses', 'ollama', 'openai-chat']
)
def test_edited_visible_image_fallback_uses_target_protocol_or_explicit_rejection(protocol):
    from fastapi import HTTPException
    from pydantic import TypeAdapter
    from openai.types.responses.easy_input_message_param import EasyInputMessageParam

    native = envelope('gemini', 'Old caption')
    native['body']['candidates'][0]['content']['parts'].insert(
        0, {'inlineData': {'mimeType': 'image/png', 'data': 'cGlj'}, 'thoughtSignature': 'invalid-old-signature'}
    )
    saved = {
        'role': 'assistant',
        'content': 'Edited caption',
        'oi_native': [native],
        'oi_native_invalidated': True,
        'files': [{'type': 'image', 'url': 'data:image/png;base64,cGlj'}],
    }
    form = {'messages': process_messages_with_output([saved, {'role': 'user', 'content': 'Describe'}])}
    if protocol == 'openai-chat':
        with pytest.raises(HTTPException) as error:
            build_request(form, binding(protocol), connection(protocol))
        assert error.value.status_code == 422 and 'generated images' in error.value.detail
        return
    _, payload = build_request(form, binding(protocol), connection(protocol))
    encoded = json.dumps(payload)
    assert 'cGlj' in encoded and 'Edited caption' in encoded
    assert 'invalid-old-signature' not in encoded and 'Old caption' not in encoded
    if protocol == 'openai-responses':
        TypeAdapter(EasyInputMessageParam).validate_python(payload['input'][0])


@pytest.mark.parametrize('retained', [1, 2])
def test_ollama_identical_parallel_tools_are_retained_once_per_canonical_call(retained):
    body = {
        'message': {
            'role': 'assistant',
            'content': 'prefix',
            'tool_calls': [
                {'function': {'name': 'lookup', 'arguments': {}}},
                {'function': {'name': 'lookup', 'arguments': {}}},
            ],
        },
        'done': True,
    }
    native = {'protocol': 'ollama', 'binding_id': 'oi:leaf', 'body': body}
    tools = [
        {'id': f't{i}', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}} for i in range(retained)
    ]
    messages = [
        {'role': 'assistant', 'content': 'prefix', 'tool_calls': tools, 'oi_native': native},
        *[{'role': 'tool', 'tool_call_id': tool['id'], 'content': 'result'} for tool in tools],
    ]
    _, payload = build_request({'messages': messages}, binding('ollama'), connection('ollama'))
    assert len(payload['messages'][0]['tool_calls']) == retained
    assert len(body['message']['tool_calls']) == 2
