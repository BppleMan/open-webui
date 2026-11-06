"""The cancel notification can release queued prompts; its snapshot must already be saved."""

import asyncio, copy, json
from contextlib import asynccontextmanager
import httpx, pytest
from open_webui.models.chats import Chat, Chats
from open_webui.models.chat_messages import ChatMessages
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_round13_parallel_cancel import parallel_response
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('notification_failure', [False, True])
async def test_terminal_notification_follows_persistence(db, monkeypatch, protocol, cancel, notification_failure):
    real_upsert = Chats.upsert_message_to_chat_by_id_and_message_id
    saved, events, ctx = context(monkeypatch, protocol)
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
    await ChatMessages.upsert_message(
        'reply', 'synthetic-chat', 'u', {'id': 'reply', 'role': 'assistant', 'output': [], 'done': False}, db=db
    )

    @asynccontextmanager
    async def transaction(cid):
        yield db, await db.get(Chat, cid)
        await db.flush()

    async def read(cid, mid):
        return (await ChatMessages.get_messages_map_by_chat_id(cid, db=db) or {}).get(mid)

    monkeypatch.setattr(Chats, '_chat_transaction', transaction)
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', real_upsert)
    monkeypatch.setattr(Chats, 'get_message_by_id_and_message_id', read)
    observed = []
    original_emit = ctx['event_emitter']

    async def emit(event):
        if event['type'] == 'chat:tasks:cancel' or (
            event['type'] == 'chat:completion' and event.get('data', {}).get('done')
        ):
            message = await read('synthetic-chat', 'reply')
            observed.append({'event': copy.deepcopy(event), 'stored_at_notification': copy.deepcopy(message)})
        if notification_failure and (event['type'] == 'chat:tasks:cancel' or event.get('data', {}).get('done')):
            raise OSError('synthetic socket publish failed')
        await original_emit(event)

    ctx['event_emitter'] = emit
    executed, payloads = [], []

    async def lookup(q):
        executed.append(q)
        if cancel and q == '2':
            raise asyncio.CancelledError()
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    def wire(req):
        payloads.append(json.loads(req.content))
        return parallel_response(protocol, req) if len(payloads) == 1 else provider_response(protocol, [{}, {}], req)

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding(protocol),
            connection(protocol),
            'publication-' + str(len(payloads)),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    response = await generate(ctx['request'], ctx['form_data'], ctx['user'])
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await middleware.process_chat_response(response, ctx)
    else:
        await middleware.process_chat_response(response, ctx)
    final = await read('synthetic-chat', 'reply')
    assert final['done'] and observed and executed == ['1', '2']
    assert observed[-1]['event']['data']['output'] == final['output']
    issues = []
    for observation in observed:
        row = observation['stored_at_notification']
        if not row.get('done') or row.get('output') != observation['event']['data']['output']:
            issues.append(
                {
                    'terminal_before_saved': observation['event']['type'],
                    'stored_done': row.get('done'),
                    'stored_result_count': len(
                        [i for i in row.get('output', []) if i['type'] == 'function_call_output']
                    ),
                }
            )
    assert not issues, issues
