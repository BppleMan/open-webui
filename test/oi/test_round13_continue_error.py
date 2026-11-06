"""A successful Continue must replace the prior attempt's error state."""

import copy
import json
from contextlib import asynccontextmanager
import httpx
import pytest
from open_webui.models.chats import Chat, Chats
from open_webui.models.chat_messages import ChatMessages
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from open_webui.utils.oi.runtime import SSEDecoder
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection
from test.oi.test_round13_stream_failures import response_for


def success_nonstream(protocol, req):
    raw = response_for(protocol, req, False, 'success')
    if protocol == 'ollama':
        body = json.loads(raw.content)
    else:
        state = protocols.NativeStream(protocol)
        for event in SSEDecoder().feed(raw.content):
            state.feed(event)
        body = state.body
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('prior_error', [False, True])
@pytest.mark.parametrize('new_stream', [False, True])
@pytest.mark.parametrize('new_failure', [False, True])
async def test_successful_continue_replaces_prior_error(
    db, monkeypatch, protocol, prior_error, new_stream, new_failure
):
    real_upsert = Chats.upsert_message_to_chat_by_id_and_message_id
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

    def real_storage():
        monkeypatch.setattr(Chats, '_chat_transaction', transaction)
        monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', real_upsert)
        monkeypatch.setattr(Chats, 'get_message_by_id_and_message_id', read)

    first, first_events, ctx = context(monkeypatch, protocol)
    real_storage()
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'old-attempt',
        httpx.MockTransport(lambda req: response_for(protocol, req, False, 'error' if prior_error else 'success')),
    )
    await middleware.process_chat_response(response, ctx)
    first = await read('synthetic-chat', 'reply')
    assert bool(first.get('error')) == prior_error
    first.update(id='reply', role='assistant', model='oi:leaf')
    saved, events, ctx = context(monkeypatch, protocol, first, assistant_message_id='reply')
    real_storage()
    ctx['form_data']['stream'] = new_stream
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'continue-attempt',
        httpx.MockTransport(
            lambda req: (
                httpx.Response(503, json={'error': {'message': 'new attempt failure'}})
                if new_failure
                else response_for(protocol, req, False, 'success')
                if new_stream
                else success_nonstream(protocol, req)
            )
        ),
    )
    await middleware.process_chat_response(response, ctx)
    saved = await read('synthetic-chat', 'reply')
    legacy = (await db.get(Chat, 'synthetic-chat')).chat['history']['messages']['reply']
    assert saved.get('error') == legacy.get('error')
    finals = [e['data'] for e in events if e['type'] == 'chat:completion' and e.get('data', {}).get('done')]
    assert saved.get('done') and finals
    assert bool(finals[-1].get('error')) == new_failure
    assert middleware.get_output_text(saved['output']).count('done') == (1 if new_failure else 2)
    assert bool(saved.get('error')) == new_failure
    if new_failure:
        assert 'new attempt failure' in str(saved['error'])
    else:
        assert 'error' in finals[-1] and finals[-1]['error'] is None
