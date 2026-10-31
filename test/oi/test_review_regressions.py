import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.models.customization import Connection, ModelBinding
from open_webui.models.models import Models
from open_webui.routers import customization
from open_webui.utils.middleware import normalize_messages_for_model, process_messages_with_output
from open_webui.utils.models import check_model_access
from open_webui.utils.oi.protocols import NativeStream, build_request, generate
from open_webui.utils.oi.runtime import SSEDecoder


def binding(protocol='anthropic'):
    return SimpleNamespace(
        id='oi:leaf',
        upstream_id='synthetic-model',
        protocol=protocol,
        capabilities={'output_limit': 4096},
        params={},
        pricing={},
    )


def connection(protocol='anthropic'):
    return SimpleNamespace(
        id='connection',
        protocol=protocol,
        template=protocol,
        base_url='https://synthetic.invalid/v1',
        credentials='',
        settings={},
    )


def saved_message(model='oi:leaf'):
    return {
        'role': 'assistant',
        'model': model,
        'content': 'answer',
        'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'answer'}]}],
        'oi_native': [
            {
                'call_id': 'call-1',
                'binding_id': 'oi:leaf',
                'protocol': 'anthropic',
                'body': {
                    'content': [
                        {'type': 'thinking', 'thinking': 'reason', 'signature': 'synthetic-signature'},
                        {'type': 'text', 'text': 'answer'},
                    ]
                },
            }
        ],
    }


def test_preset_replays_native_signature():
    reconstructed = process_messages_with_output([saved_message('rp-preset')])
    _, payload = build_request({'messages': reconstructed}, binding(), connection())
    assert any(block.get('signature') == 'synthetic-signature' for block in payload['messages'][0]['content'])


def test_native_preset_preserves_mid_conversation_instruction_order():
    messages = [
        {'role': 'system', 'content': 'initial'},
        {'role': 'user', 'content': 'turn one'},
        {'role': 'assistant', 'content': 'answer'},
        {'role': 'system', 'content': 'explicit later instruction'},
        {'role': 'user', 'content': 'turn two'},
    ]
    normalized = normalize_messages_for_model(
        {'model': 'roleplay-preset', 'messages': copy.deepcopy(messages)}, {'oi_base_binding': 'oi:leaf'}
    )
    assert normalized['messages'] == messages


def test_second_normalization_keeps_attached_native_history():
    reconstructed = process_messages_with_output([saved_message()])
    assert reconstructed[0].get('oi_native')
    # The tool-followup path sends its already-normalized history through this function again.
    reconstructed = process_messages_with_output(reconstructed)
    _, payload = build_request({'messages': reconstructed}, binding(), connection())
    assert any(block.get('signature') == 'synthetic-signature' for block in payload['messages'][0]['content'])


def test_anthropic_partial_usage_is_observable_before_message_stop():
    state = NativeStream('anthropic')
    events = state.feed(
        {'type': 'message_start', 'message': {'content': [], 'usage': {'input_tokens': 123, 'output_tokens': 1}}}
    )
    events += state.feed(
        {'type': 'message_delta', 'delta': {'stop_reason': 'max_tokens'}, 'usage': {'output_tokens': 20}}
    )
    assert any(event.get('oi_usage', {}).get('input_tokens') == 123 for event in events)


@pytest.mark.parametrize(
    'protocol,body',
    [
        ('anthropic', {'content': [{'type': 'text', 'text': 'partial'}], 'stop_reason': 'max_tokens'}),
        (
            'gemini',
            {
                'candidates': [
                    {'content': {'role': 'model', 'parts': [{'text': 'partial'}]}, 'finishReason': 'MAX_TOKENS'}
                ]
            },
        ),
    ],
)
def test_native_length_stop_is_not_mapped_to_success(protocol, body):
    result = NativeStream(protocol).nonstream(body)
    assert result['choices'][0]['finish_reason'] == 'length'


@pytest.mark.asyncio
async def test_ollama_stream_emits_outer_tool_index():
    raw = {
        'model': 'synthetic-model',
        'message': {
            'role': 'assistant',
            'content': '',
            'tool_calls': [{'function': {'index': 0, 'name': 'lookup', 'arguments': {'q': 'synthetic'}}}],
        },
        'done': True,
        'done_reason': 'stop',
        'prompt_eval_count': 10,
        'eval_count': 3,
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(raw) + '\n'))
    response = await generate(
        SimpleNamespace(state=SimpleNamespace(metadata={})),
        {'model': 'oi:leaf', 'messages': [{'role': 'user', 'content': 'hello'}], 'stream': True},
        SimpleNamespace(id='u'),
        binding('ollama'),
        connection('ollama'),
        'call',
        transport=transport,
    )
    decoder, events = SSEDecoder(), []
    async for chunk in response.body_iterator:
        events.extend(decoder.feed(chunk))
    tool = events[0]['choices'][0]['delta']['tool_calls'][0]
    assert tool.get('index') == 0  # middleware ignores tool calls without this outer index


@pytest.mark.asyncio
async def test_binding_api_respects_model_visibility(db, monkeypatch):
    db.add(
        Connection(
            id='c',
            owner_id='admin',
            name='Shared connection',
            template='openai-compatible',
            protocol='openai-chat',
            base_url='https://synthetic.invalid/v1',
            credentials='',
            settings={},
            shared=True,
            enabled=True,
            revision=1,
            created_at=1,
            updated_at=1,
        )
    )
    db.add(
        ModelBinding(
            id='oi:restricted',
            connection_id='c',
            upstream_id='private-model',
            protocol='openai-chat',
            name='Restricted model',
            enabled=True,
            available=True,
            capabilities={'legacy_model_id': 'restricted', 'legacy_unregistered': True},
            pricing={},
            params={'system': 'private synthetic instruction'},
            aliases=['restricted'],
            updated_at=1,
        )
    )
    await db.flush()
    user = SimpleNamespace(id='ordinary-user', role='user')
    monkeypatch.setattr(Models, 'get_model_by_id', AsyncMock(return_value=None))
    with pytest.raises(Exception, match='access is not configured'):
        await check_model_access(user, {'id': 'oi:restricted', 'oi_binding': True}, db=db)
    result = await customization.bindings(user=user, db=db)
    assert result == []


@pytest.mark.asyncio
async def test_save_as_copy_invalidates_original_native_body(db, monkeypatch):
    from contextlib import asynccontextmanager

    from open_webui.models.chats import Chat, ChatForm
    from open_webui.routers import chats

    original = {**saved_message(), 'id': 'original', 'done': True}
    edited = copy.deepcopy(original)
    edited.update(id='copy', content='EDITED')
    edited['output'][0]['content'][0]['text'] = 'EDITED'
    fake_chat = Chat(
        id='chat',
        user_id='u',
        title='Synthetic',
        chat={'history': {'messages': {'original': original}, 'currentId': 'original'}},
        created_at=1,
        updated_at=1,
    )
    db.add(fake_chat)
    await db.flush()

    @asynccontextmanager
    async def transaction(_id):
        yield db, fake_chat
        await db.flush()

    monkeypatch.setattr(chats.Chats, 'get_chat_by_id_and_user_id', AsyncMock(return_value=fake_chat))
    monkeypatch.setattr(chats.Chats, '_chat_transaction', transaction)
    monkeypatch.setattr(chats, 'publish_event', AsyncMock())
    form = ChatForm(chat={'history': {'messages': {'original': original, 'copy': edited}, 'currentId': 'copy'}})
    await chats.update_chat_by_id(SimpleNamespace(), 'chat', form, SimpleNamespace(id='u'), db)
    reconstructed = process_messages_with_output([form.chat['history']['messages']['copy']])
    _, payload = build_request({'messages': reconstructed}, binding(), connection())
    assert payload['messages'][0]['content'][-1]['text'] == 'EDITED'


@pytest.mark.asyncio
async def test_interrupted_anthropic_call_keeps_confirmed_usage(db, monkeypatch):
    from contextlib import asynccontextmanager

    from open_webui.models.customization import ModelCall
    from open_webui.utils.oi import runtime
    from sqlalchemy import select

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    frames = [
        {'type': 'message_start', 'message': {'content': [], 'usage': {'input_tokens': 123, 'output_tokens': 1}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'partial'}},
    ]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames))
    )
    request = SimpleNamespace(state=SimpleNamespace(metadata={}))
    form = {'model': 'oi:leaf', 'messages': [{'role': 'user', 'content': 'hello'}], 'stream': True}
    user, b, c = SimpleNamespace(id='u'), binding(), connection()
    result = await runtime.track_call(
        request, form, user, lambda call_id: generate(request, form, user, b, c, call_id, transport=transport), b, c
    )
    async for _ in result.body_iterator:
        pass
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert call.status == 'failed'
    assert call.usage.get('input_tokens') == 123


@pytest.mark.asyncio
async def test_catalog_public_projection_filters_access_and_private_params(db, monkeypatch):
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.groups import Groups
    from open_webui.utils.models import get_filtered_models
    from open_webui.utils.oi import catalog

    c = Connection(
        id='c',
        owner_id='admin',
        name='Shared',
        template='openai-compatible',
        protocol='openai-chat',
        base_url='https://synthetic.invalid/v1',
        credentials='',
        settings={},
        shared=True,
        enabled=True,
        revision=1,
        created_at=1,
        updated_at=1,
    )

    def make(id, caps):
        return ModelBinding(
            id=id,
            connection_id='c',
            upstream_id=id,
            protocol='openai-chat',
            name=id,
            enabled=True,
            available=True,
            capabilities=caps,
            pricing={},
            aliases=[],
            updated_at=1,
            params={
                'system': 'private synthetic system',
                'provider_options': {'private': 'synthetic'},
                'model_controls': {
                    'effort': {
                        'label': 'Effort',
                        'options': {'high': {'label': 'High', 'params': {'secret': 'synthetic'}}},
                    }
                },
            },
        )

    db.add_all(
        [c, make('oi:denied', {'legacy_model_id': 'denied', 'legacy_unregistered': True}), make('oi:allowed', {})]
    )
    await db.flush()
    monkeypatch.setattr(Models, 'get_model_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(AccessGrants, 'get_accessible_resource_ids', AsyncMock(return_value=[]))
    ordinary = SimpleNamespace(id='ordinary', role='user')
    raw = await customization.bindings(user=ordinary, db=db)
    public = await customization.binding_catalog(user=ordinary, db=db)
    filtered = await get_filtered_models(
        await catalog.model_catalog(db, SimpleNamespace(id='admin', role='admin')), ordinary, db=db
    )
    assert [m['id'] for m in raw] == [m['id'] for m in public] == [m['id'] for m in filtered] == ['oi:allowed']
    assert 'private synthetic system' not in json.dumps(raw + public)
    assert raw[0]['params']['model_controls']['effort']['options'] == {'high': {'label': 'High'}}
    admin = await customization.bindings(user=SimpleNamespace(id='admin', role='admin'), db=db)
    assert len(admin) == 2 and admin[0]['params']['system'] == 'private synthetic system'


def test_copy_and_preset_replay_preserve_only_unchanged_matching_binding():
    from open_webui.utils.oi.replay import reconcile_native_edit

    old = {**saved_message('nested-rp-preset'), 'id': 'source'}
    copy_message = copy.deepcopy(old)
    copy_message['id'] = 'copy'
    reconcile_native_edit(copy_message, {'source': old})
    normalized = process_messages_with_output(process_messages_with_output([copy_message]))
    _, payload = build_request({'messages': normalized}, binding(), connection())
    assert payload['messages'][0]['content'][0]['signature'] == 'synthetic-signature'
    changed_binding = binding()
    changed_binding.id = 'oi:other'
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        build_request({'messages': normalized}, changed_binding, connection())
    assert error.value.status_code == 422
    copy_message['content'] = 'new body'
    copy_message['output'][0]['content'][0]['text'] = 'new body'
    copy_message['previous_response_id'] = 'old'
    reconcile_native_edit(copy_message, {'source': old})
    assert copy_message['oi_native_invalidated'] and 'previous_response_id' not in copy_message
    _, edited = build_request({'messages': process_messages_with_output([copy_message])}, binding(), connection())
    assert 'new body' in json.dumps(edited) and 'synthetic-signature' not in json.dumps(edited)
    assert old['content'] == 'answer' and not old.get('oi_native_invalidated')


@pytest.mark.parametrize(
    'protocol,reason,expected,status',
    [
        ('anthropic', 'end_turn', 'stop', 'completed'),
        ('anthropic', 'max_tokens', 'length', 'incomplete'),
        ('anthropic', 'model_context_window_exceeded', 'length', 'incomplete'),
        ('anthropic', 'refusal', 'content_filter', 'blocked'),
        ('anthropic', 'pause_turn', 'pause', 'paused'),
        ('anthropic', 'tool_use', 'tool_calls', 'completed'),
        ('gemini', 'STOP', 'stop', 'completed'),
        ('gemini', 'MAX_TOKENS', 'length', 'incomplete'),
        ('gemini', 'SAFETY', 'content_filter', 'blocked'),
        ('gemini', 'CONTINUATION', 'pause', 'paused'),
        ('gemini', 'MALFORMED_FUNCTION_CALL', 'error', 'failed'),
        ('gemini', 'OTHER', 'unknown', 'incomplete'),
    ],
)
def test_native_stream_and_nonstream_endings_agree(protocol, reason, expected, status):
    block = (
        {'type': 'tool_use', 'id': 't', 'name': 'lookup', 'input': {}}
        if reason == 'tool_use'
        else {'type': 'text', 'text': 'partial'}
    )
    body = (
        {'content': [block], 'stop_reason': reason}
        if protocol == 'anthropic'
        else {'candidates': [{'content': {'parts': [{'text': 'partial'}]}, 'finishReason': reason}]}
    )
    nonstream = NativeStream(protocol).nonstream(body)
    state = NativeStream(protocol)
    if protocol == 'anthropic':
        state.feed({'type': 'message_start', 'message': {'content': []}})
        state.feed({'type': 'content_block_start', 'index': 0, 'content_block': block})
        state.feed({'type': 'message_delta', 'delta': {'stop_reason': reason}})
        last = state.feed({'type': 'message_stop'})[-1]
    else:
        last = state.feed(body)[-1]
    for result in (nonstream, last):
        assert result['choices'][0]['finish_reason'] == expected
        assert result['oi_finish'] == {'protocol': protocol, 'reason': reason, 'normalized': expected, 'status': status}


@pytest.mark.asyncio
@pytest.mark.parametrize('ending', ['normal', 'eof', 'error', 'cancel'])
async def test_partial_usage_survives_every_stream_exit(db, monkeypatch, ending):
    import asyncio
    from contextlib import asynccontextmanager

    from open_webui.models.customization import ModelCall
    from open_webui.utils.oi import runtime
    from sqlalchemy import select

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            start = {
                'type': 'message_start',
                'message': {'content': [], 'usage': {'input_tokens': 123, 'cache_read_input_tokens': 7}},
            }
            yield ('data: ' + json.dumps(start) + '\n\n').encode()
            if ending == 'cancel':
                raise asyncio.CancelledError()
            if ending == 'error':
                yield b'data: {"type":"error","error":{"message":"synthetic failure"}}\n\n'
            if ending == 'normal':
                yield (
                    b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                    b'"usage":{"output_tokens":20}}\n\n'
                )
                yield b'data: {"type":"message_delta","delta":{},"usage":{"output_tokens":20}}\n\n'
                yield b'data: {"type":"message_stop"}\n\n'

    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=Body()))
    request = SimpleNamespace(state=SimpleNamespace(metadata={}))
    form = {'model': 'oi:leaf', 'messages': [{'role': 'user', 'content': 'hello'}], 'stream': True}
    b, c, u = binding(), connection(), SimpleNamespace(id='u')
    result = await runtime.track_call(
        request, form, u, lambda call_id: generate(request, form, u, b, c, call_id, transport), b, c
    )
    try:
        async for _ in result.body_iterator:
            pass
    except asyncio.CancelledError:
        assert ending == 'cancel'
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert call.usage['input_tokens'] == 130 and call.usage['cache_read_tokens'] == 7
    if ending == 'normal':
        assert call.status == 'completed' and call.usage['output_tokens'] == 20 and call.usage['total_tokens'] == 150
    else:
        assert call.status == ('cancelled' if ending == 'cancel' else 'failed')
        assert 'output_tokens' not in call.usage and call.actual_usd is None and call.estimated_usd is None
