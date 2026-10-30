import copy
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.chats import Chat, ChatModel
from open_webui.models.customization import ModelCall
from open_webui.routers import chats as chat_routes
from open_webui.utils.middleware import process_messages_with_output
from open_webui.utils.oi import accounting, runtime
from open_webui.utils.oi.protocols import build_request, generate
from test.oi.test_accounting import answer
from test.oi.test_review_regressions import binding, connection, saved_message


@pytest.mark.asyncio
async def test_nested_fork_earlier_message_keeps_its_original_cost_boundary(db, monkeypatch):
    # A costs $0.03 after m1 and $0.07 after m2. B forks at m2, then C forks B at m1.
    await answer(db, 'A', 'm1', '0.03')
    await answer(db, 'A', 'm2', '0.04')
    await accounting.inherit_snapshot(db, 'u', 'B', 'A', 'm2')
    messages = {
        'u1': {'id': 'u1', 'role': 'user', 'content': 'hello', 'parentId': None, 'childrenIds': ['m1']},
        'm1': {'id': 'm1', 'role': 'assistant', 'content': 'one', 'parentId': 'u1', 'childrenIds': ['u2']},
        'u2': {'id': 'u2', 'role': 'user', 'content': 'next', 'parentId': 'm1', 'childrenIds': ['m2']},
        'm2': {'id': 'm2', 'role': 'assistant', 'content': 'two', 'parentId': 'u2', 'childrenIds': []},
    }
    source = ChatModel(
        id='B',
        user_id='u',
        title='B',
        chat={'history': {'messages': messages, 'currentId': 'm2'}},
        created_at=1,
        updated_at=1,
        current_message_id='m2',
    )
    monkeypatch.setattr(chat_routes, 'require_chat_import_permission', AsyncMock())
    monkeypatch.setattr(chat_routes, 'has_active_tasks', AsyncMock(return_value=False))
    monkeypatch.setattr(chat_routes, 'publish_event', AsyncMock())
    monkeypatch.setattr(chat_routes.Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=source))
    monkeypatch.setattr(chat_routes.Chats, 'get_messages_map_by_chat_id', AsyncMock(return_value=messages))

    async def insert(chat_id, user_id, form, **kwargs):
        return ChatModel(id=chat_id, user_id=user_id, title='C', chat=form.chat, created_at=1, updated_at=1)

    monkeypatch.setattr(chat_routes.Chats, 'insert_new_chat', insert)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
    result = await chat_routes.fork_chat_by_id(
        request, 'B', chat_routes.ForkForm(message_id='m1'), SimpleNamespace(id='u', role='admin'), db
    )
    total = await accounting.get_totals(db, result.id)
    assert Decimal(total['actual_usd']) == Decimal('0.03'), total


def test_continue_same_message_preserves_both_native_envelopes():
    saved = saved_message()
    second = copy.deepcopy(saved['oi_native'][0])
    second.update(call_id='call-2')
    second['body']['content'] = [{'type': 'text', 'text': 'more'}]
    saved['oi_native'].append(second)
    # This is the full_output() result when Continue appends text to the old reply.
    saved['output'][0]['content'].append({'type': 'output_text', 'text': 'more'})
    saved['content'] = 'answer\nmore'
    messages = process_messages_with_output([saved])
    _, payload = build_request({'messages': messages}, binding(), connection())
    assert 'synthetic-signature' in json.dumps(payload), payload


def test_openai_chat_replays_reasoning_details_on_same_binding():
    details = [{'type': 'reasoning.encrypted', 'data': 'opaque-provider-state', 'id': 'r1', 'index': 0}]
    message = {
        'role': 'assistant',
        'content': '',
        'reasoning_details': details,
        'tool_calls': [{'id': 't1', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}],
    }
    # The canonical history and the captured native response both carry these details.
    message['oi_native'] = {
        'binding_id': 'oi:leaf',
        'protocol': 'openai-chat',
        'body': {'choices': [{'message': copy.deepcopy(message)}]},
    }
    _, payload = build_request(
        {'messages': [message, {'role': 'tool', 'tool_call_id': 't1', 'content': 'result'}]},
        binding('openai-chat'),
        connection('openai-chat'),
    )
    assert payload['messages'][0].get('reasoning_details') == details, payload


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_temporary_native_signature_reaches_client_for_next_turn(stream):
    native = {
        'id': 'provider-reply',
        'content': [
            {'type': 'thinking', 'thinking': 'reason', 'signature': 'temporary-signature'},
            {'type': 'text', 'text': 'answer'},
        ],
        'stop_reason': 'end_turn',
        'usage': {'input_tokens': 1, 'output_tokens': 2},
    }
    meta = {'chat_id': 'local:test-socket', 'message_id': 'reply', 'oi_temporary_id': 'stable-temp'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=meta))
    frames = [
        {'type': 'message_start', 'message': {'content': [], 'usage': {'input_tokens': 1}}},
        *(
            {'type': 'content_block_start', 'index': i, 'content_block': block}
            for i, block in enumerate(native['content'])
        ),
        {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 2}},
        {'type': 'message_stop'},
    ]
    response = (
        httpx.Response(200, content=''.join('data: ' + json.dumps(f) + '\n\n' for f in frames))
        if stream
        else httpx.Response(200, json=native)
    )
    result = await generate(
        request,
        {'model': 'oi:leaf', 'messages': [{'role': 'user', 'content': 'hi'}], 'metadata': meta, 'stream': stream},
        SimpleNamespace(id='u'),
        binding(),
        connection(),
        'temp-call',
        httpx.MockTransport(lambda _: response),
    )
    client_data = ''.join([chunk async for chunk in result.body_iterator]) if stream else json.dumps(result)
    from open_webui.utils.oi.temporary_native import cipher

    frames = (
        [json.loads(line[6:]) for line in client_data.splitlines() if line.startswith('data: {')]
        if stream
        else [json.loads(client_data)]
    )
    capsule = next(f['oi_native_state'] for f in frames if f.get('oi_native_state'))
    assert 'temporary-signature' in cipher().decrypt(capsule.encode()).decode()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,expected', [('length', 'incomplete'), ('content_filter', 'blocked')])
async def test_openai_finish_reason_controls_ledger_status(db, monkeypatch, reason, expected):
    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)

    async def dispatch(_):
        return {
            'choices': [{'message': {'role': 'assistant', 'content': 'partial'}, 'finish_reason': reason}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 2},
        }

    req = SimpleNamespace(state=SimpleNamespace(metadata={}))
    await runtime.track_call(req, {'model': 'oi:leaf', 'messages': []}, SimpleNamespace(id='u'), dispatch)
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert call.status == expected, {'finish_reason': reason, 'status': call.status, 'usage': call.usage}


@pytest.mark.asyncio
async def test_continuing_legacy_chat_preserves_preexisting_analytics(db):
    db.add(Chat(id='legacy', user_id='u', title='old', chat={}, created_at=1, updated_at=1))
    db.add(
        ChatMessage(
            id='legacy-old',
            chat_id='legacy',
            user_id='u',
            role='assistant',
            model_id='old-model',
            usage={'input_tokens': 100, 'output_tokens': 20},
            created_at=1,
            updated_at=1,
        )
    )
    await db.flush()
    before = await ChatMessages.get_token_usage_by_model(db=db)
    await answer(db, 'legacy', 'new', '0.01')
    after = await ChatMessages.get_token_usage_by_model(db=db)
    assert after.get('old-model') == before['old-model'], {'before': before, 'after': after}


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['anthropic', 'gemini'])
async def test_temporary_state_roundtrips_between_independent_requests(protocol):
    from open_webui.utils.oi.temporary_native import restore, wrap_emitter
    from open_webui.utils.oi.protocols import NativeStream

    first = (
        {
            'content': [
                {'type': 'thinking', 'thinking': 'reason', 'signature': 'signed-state'},
                {'type': 'text', 'text': 'answer'},
            ],
            'stop_reason': 'end_turn',
        }
        if protocol == 'anthropic'
        else {
            'candidates': [
                {
                    'content': {'role': 'model', 'parts': [{'text': 'answer', 'thoughtSignature': 'signed-state'}]},
                    'finishReason': 'STOP',
                }
            ]
        }
    )
    meta = {'chat_id': 'local:socket', 'message_id': 'reply', 'oi_temporary_id': 'temp'}
    user = SimpleNamespace(id='u')
    request = SimpleNamespace(state=SimpleNamespace(metadata=meta))
    result = await generate(
        request,
        {'model': 'oi:leaf', 'messages': [{'role': 'user', 'content': 'hi'}], 'metadata': meta, 'stream': False},
        user,
        binding(protocol),
        connection(protocol),
        'first',
        httpx.MockTransport(lambda _: httpx.Response(200, json=first)),
    )
    saved = {'id': 'reply', **result['choices'][0]['message'], 'oi_native_state': result['oi_native_state']}
    restore(saved, 'u', 'temp')
    canonical = [{**saved, 'oi_native': saved['oi_native'][0]}]
    _, body = build_request({'messages': canonical}, binding(protocol), connection(protocol))
    assert 'signed-state' in json.dumps(body)
    # Actual UI completion transports an output-bound capsule, including Continue history.
    output = [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'answer'}]}]
    events = []

    async def emit(event):
        events.append(event)

    await wrap_emitter(emit, request, meta, user)({'type': 'chat:completion', 'data': {'done': True, 'output': output}})
    saved = {'id': 'reply', 'output': output, 'oi_native_state': events[0]['data']['oi_native_state']}
    assert restore(saved, 'u', 'temp')[0]['call_id'] == 'first'


@pytest.mark.parametrize('nested', [False, True])
def test_streamed_reasoning_details_survive_later_summary_chunks(nested):
    from open_webui.utils.oi.protocols import NativeStream

    state = NativeStream('openai-chat')
    for details in [
        [{'index': 0, 'type': 'reasoning.encrypted', 'data': 'opaque-state'}],
        [{'index': 1, 'type': 'reasoning.summary', 'summary': 'first '}],
        [{'index': 1, 'type': 'reasoning.summary', 'summary': 'second'}],
    ]:
        delta = {'reasoning_details': details}
        if nested:
            delta = {'provider_specific_fields': delta}
        state.feed({'choices': [{'delta': delta}]})
    message = {
        'role': 'assistant',
        'content': '',
        'oi_native': {'binding_id': 'oi:leaf', 'protocol': 'openai-chat', 'body': state.body},
    }
    _, body = build_request({'messages': [message]}, binding('openai-chat'), connection('openai-chat'))
    replay = body['messages'][0]
    details = (replay['provider_specific_fields'] if nested else replay)['reasoning_details']
    assert details[0]['data'] == 'opaque-state'
    assert details[1]['summary'] == 'first second'


@pytest.mark.asyncio
async def test_imported_usage_is_not_a_new_event_but_later_calls_are(db):
    from open_webui.utils.oi import statistics

    db.add(Chat(id='import', user_id='u', title='Imported', chat={}, created_at=1, updated_at=1))
    await ChatMessages.upsert_message(
        'old',
        'import',
        'u',
        {
            'id': 'old',
            'role': 'assistant',
            'model': 'old-model',
            'content': 'copy',
            'usage': {'input_tokens': 100, 'output_tokens': 20},
        },
        db=db,
    )
    await accounting.set_baseline(db, 'u', 'import', 'imported-history')
    assert await statistics.by(db) == {}
    await answer(db, 'import', 'new', '0.03')
    totals = await statistics.by(db)
    assert sum(row['count'] for row in totals.values()) == 1
    assert sum(row['input_tokens'] for row in totals.values()) == 1
