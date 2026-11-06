"""Responses terminal metadata must survive normalized rows and portable archives."""

import copy
import json
from contextlib import asynccontextmanager
import httpx
import pytest
from open_webui.models.chats import Chat, Chats
from open_webui.models.chat_messages import ChatMessages, ChatMessage
from open_webui.utils import middleware
from open_webui.utils.oi import protocols, runtime, archive
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['completed', 'incomplete', 'failed'])
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('destination', ['u', 'other-user'])
@pytest.mark.parametrize('legacy_only', [False, True])
async def test_terminal_metadata_survives_both_message_stores_and_archive(
    db, monkeypatch, status, streaming, destination, legacy_only
):
    real_upsert = Chats.upsert_message_to_chat_by_id_and_message_id
    saved, events, ctx = context(monkeypatch, 'openai-responses')
    ctx['form_data']['stream'] = streaming
    db.add(
        Chat(
            id='synthetic-chat',
            user_id='u',
            title='synthetic',
            created_at=1,
            updated_at=1,
            chat={'history': {'currentId': 'reply', 'messages': {}}},
        )
    )
    await db.flush()

    @asynccontextmanager
    async def transaction(cid):
        yield db, await db.get(Chat, cid)
        await db.flush()

    async def read(cid, mid):
        return (await ChatMessages.get_messages_map_by_chat_id(cid, db=db) or {}).get(mid)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(Chats, '_chat_transaction', transaction)
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', real_upsert)
    monkeypatch.setattr(Chats, 'get_message_by_id_and_message_id', read)
    monkeypatch.setattr(runtime, 'get_async_db', session)
    body = {
        'id': 'response',
        'model': 'synthetic-model',
        'status': status,
        'output': [
            {
                'type': 'message',
                'id': 'msg',
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': 'preserved partial'}],
            }
        ],
        'usage': {'input_tokens': 10, 'output_tokens': 2},
    }
    if status == 'incomplete':
        body['incomplete_details'] = {'reason': 'max_output_tokens'}
    if status == 'failed':
        body['error'] = {'code': 'server_error', 'message': 'synthetic failure'}
    b, c = binding('openai-responses'), connection('openai-responses')
    transport = httpx.MockTransport(
        lambda _: (
            httpx.Response(
                200, content='data: ' + json.dumps({'type': 'response.' + status, 'response': body}) + '\n\n'
            )
            if streaming
            else httpx.Response(200, json=body)
        )
    )

    async def dispatch(cid):
        return await protocols.generate(ctx['request'], ctx['form_data'], ctx['user'], b, c, cid, transport)

    response = await runtime.track_call(ctx['request'], ctx['form_data'], ctx['user'], dispatch, b, c)
    await middleware.process_chat_response(response, ctx)
    legacy = copy.deepcopy((await db.get(Chat, 'synthetic-chat')).chat['history']['messages']['reply'])
    if legacy_only:
        row = await db.get(ChatMessage, 'synthetic-chat-reply')
        row.oi_state = {k: v for k, v in row.oi_state.items() if k not in {'response_status', 'incomplete_details'}}
        await db.flush()
    normalized = await read('synthetic-chat', 'reply')
    assert legacy['response_status'] == status
    assert legacy.get('incomplete_details') == body.get('incomplete_details')
    assert normalized['finish_reason'] == legacy['finish_reason']
    assert normalized['output'] == legacy['output'] and normalized['usage'] == legacy['usage']
    manifest = await archive.export_records(db, 'u', 'synthetic-chat')
    restored = await archive.restore_records(db, destination, copy.deepcopy(manifest), 'restored')
    restored_message = (await ChatMessages.get_messages_map_by_chat_id(restored.id, db=db))['reply']
    archived = manifest['chat']['history']['messages']['reply']
    issues = []
    for stage, message in [('normalized', normalized), ('archive', archived), ('restored', restored_message)]:
        if message.get('response_status') != status:
            issues.append({'stage': stage, 'missing_response_status': status})
        if status == 'incomplete' and message.get('incomplete_details') != body['incomplete_details']:
            issues.append({'stage': stage, 'missing_incomplete_details': body['incomplete_details']})
        assert message['finish_reason'] == legacy['finish_reason'] and message['usage'] == legacy['usage']
    assert not issues, issues


@pytest.mark.asyncio
@pytest.mark.parametrize('canonical_status', [None, 'completed', 'changed-output'])
async def test_legacy_terminal_fallback_never_overrides_current_output(db, canonical_status):
    output = [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'old'}]}]
    legacy = {
        'id': 'reply',
        'role': 'assistant',
        'content': 'old',
        'output': output,
        'response_status': 'failed',
        'incomplete_details': {'reason': 'old'},
    }
    db.add(
        Chat(
            id='compat',
            user_id='u',
            title='synthetic',
            created_at=1,
            updated_at=1,
            chat={'history': {'messages': {'reply': legacy}}},
        )
    )
    await db.flush()
    data = copy.deepcopy(legacy)
    data.pop('response_status')
    data.pop('incomplete_details')
    if canonical_status == 'changed-output':
        data['output'] = [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'new'}]}]
    else:
        data.update(response_status=canonical_status, incomplete_details=None)
    await ChatMessages.upsert_message('reply', 'compat', 'u', data, db=db)
    result = (await ChatMessages.get_messages_map_by_chat_id('compat', db=db))['reply']
    assert result.get('response_status') == ('completed' if canonical_status == 'completed' else None)
    assert result.get('incomplete_details') is None
