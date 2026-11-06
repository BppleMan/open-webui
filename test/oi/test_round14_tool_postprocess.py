"""Successful tool return must survive an ordinary error during attachment postprocessing."""

import copy, json
from contextlib import asynccontextmanager
from open_webui.models.chats import Chat, Chats
from open_webui.models.chat_messages import ChatMessages
import httpx, pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7X8AAAAASUVORK5CYII='


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('storage_failure', [False, True])
@pytest.mark.parametrize('phase', ['storage', 'processing', 'terminal_event', 'publication'])
@pytest.mark.parametrize('temporary', [False, True])
async def test_returned_result_survives_attachment_failure(
    db, monkeypatch, protocol, storage_failure, phase, temporary
):
    real_upsert = Chats.upsert_message_to_chat_by_id_and_message_id
    saved, events, ctx = context(monkeypatch, protocol)
    if not temporary:
        db.add(
            Chat(
                id='synthetic-chat',
                user_id='u',
                title='synthetic',
                created_at=1,
                updated_at=1,
                chat={'history': {'messages': {}}},
            )
        )
        await db.flush()

        @asynccontextmanager
        async def transaction(cid):
            yield db, await db.get(Chat, cid)
            await db.flush()

        async def read(cid, mid):
            return (await ChatMessages.get_messages_map_by_chat_id(cid, db=db) or {}).get(mid)

        monkeypatch.setattr(Chats, '_chat_transaction', transaction)
        monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', real_upsert)
        monkeypatch.setattr(Chats, 'get_message_by_id_and_message_id', read)
        await real_upsert('synthetic-chat', 'reply', {'role': 'assistant', 'done': False, 'output': []})
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='tool-postprocess-temp')
    executed, payloads = [], []

    async def lookup(q):
        executed.append(q)
        return [{'type': 'text', 'text': 'result:' + q}, {'type': 'image', 'mimeType': 'image/png', 'data': PNG}]

    ctx['metadata']['tools'] = {
        'lookup': {
            'type': 'mcp',
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    async def store(request, data, metadata, user):
        if storage_failure and phase == 'storage':
            raise OSError('synthetic attachment storage unavailable')
        return data

    monkeypatch.setattr(middleware, 'get_file_url_from_base64', store)
    original_process = middleware.process_tool_result

    async def process(*args, **kwargs):
        if storage_failure and phase == 'processing':
            raise OSError('synthetic result conversion failed')
        return await original_process(*args, **kwargs)

    monkeypatch.setattr(middleware, 'process_tool_result', process)
    original_terminal = middleware.terminal_event_handler

    async def terminal(*args, **kwargs):
        if storage_failure and phase == 'terminal_event':
            raise OSError('synthetic terminal event failed')
        return await original_terminal(*args, **kwargs)

    monkeypatch.setattr(middleware, 'terminal_event_handler', terminal)
    emit = ctx['event_emitter']
    failed = False

    async def publication(event):
        nonlocal failed
        if storage_failure and phase == 'publication' and not failed and 'function_call_output' in json.dumps(event):
            failed = True
            raise OSError('synthetic publication failed')
        await emit(event)

    ctx['event_emitter'] = publication

    def wire(req):
        payloads.append(json.loads(req.content))
        return provider_response(protocol, [] if len(payloads) == 1 else [{}, {}], req)

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding(protocol),
            connection(protocol),
            'attachment-' + str(len(payloads)),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    process_error = None
    try:
        await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    except Exception as exc:
        process_error = type(exc).__name__ + ': ' + str(exc)
    if temporary:
        from open_webui.utils.oi.temporary_native import restore

        final = next(
            e['data'] for e in reversed(events) if e['type'] == 'chat:completion' and e.get('data', {}).get('done')
        )
        saved = {**copy.deepcopy(final), 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        assert restore(saved, 'u', 'tool-postprocess-temp')
    if not temporary:
        saved = await read('synthetic-chat', 'reply')
        assert saved['output'] == (await db.get(Chat, 'synthetic-chat')).chat['history']['messages']['reply']['output']
    saved.update(id='reply', role='assistant', model='oi:leaf')
    normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
    _, outbound = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
    assert 'result:1' in json.dumps(outbound)
    assert not process_error
    if storage_failure:
        assert saved['done'] and saved.get('error')
        assert middleware.publish_chat_finished_event.await_count == 0
        assert middleware.background_tasks_handler.await_count == 0
    completed = [
        i for i in saved.get('output', []) if i.get('type') == 'function_call_output' and i.get('status') == 'completed'
    ]
    assert executed
    if not storage_failure:
        assert not process_error and len(executed) == (2 if protocol == 'ollama' else 1)
    assert len(completed) == len(executed), {
        'returned_tools': executed,
        'stored_results': completed,
        'error': process_error,
    }
