"""Native privacy and tool continuation regressions. Synthetic records only."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.models.chats import Chat, Chats
from open_webui.routers import chats as routes
from open_webui.utils.oi.native_privacy import replay_envelope
from open_webui.utils.middleware import process_messages_with_output
from open_webui.utils.oi.protocols import build_request
from test.oi.test_review_regressions import binding, connection


async def private_collaborator_chat(db):
    message = {
        'id': 'reply',
        'role': 'assistant',
        'user_id': 'writer',
        'content': 'Visible reply',
        'oi_native': [
            {
                'call_id': 'writer-call',
                'binding_id': 'oi:leaf',
                'protocol': 'openai-responses',
                'caller_id': 'writer',
                'configuration_owner_id': 'writer',
                'body': {
                    'instructions': 'writer-private-configuration',
                    'output': [
                        {
                            'type': 'message',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': 'Visible reply'}],
                        }
                    ],
                },
            }
        ],
    }
    row = Chat(
        id='shared',
        user_id='owner',
        title='Shared',
        folder_id='folder',
        pinned=False,
        chat={'history': {'currentId': 'reply', 'messages': {'reply': message}}},
        created_at=1,
        updated_at=1,
    )
    db.add(row)
    await db.commit()
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['pin', 'folder'])
async def test_other_chat_responses_apply_native_privacy(db, monkeypatch, endpoint):
    row = await private_collaborator_chat(db)
    user = SimpleNamespace(id='owner', role='user')
    safe = await routes.private_chat_response(row, user.id, db)
    assert 'writer-private-configuration' not in safe.model_dump_json()
    monkeypatch.setattr(routes, 'publish_event', AsyncMock())
    if endpoint == 'pin':
        result = await routes.pin_chat_by_id(SimpleNamespace(), row.id, user, db)
        encoded = result.model_dump_json()
    else:
        monkeypatch.setattr(routes.Folders, 'get_children_folders_by_id_and_user_id', AsyncMock(return_value=[]))
        result = await routes.get_chats_by_folder_id('folder', user, db)
        assert len(result) == 1
        encoded = json.dumps([r.model_dump() for r in result])
    assert 'writer-private-configuration' not in encoded, encoded


def continued_tool_reply(protocol):
    if protocol == 'anthropic':
        bodies = [
            {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'lookup', 'input': {}}], 'stop_reason': 'tool_use'},
            {'content': [{'type': 'text', 'text': 'answer'}], 'stop_reason': 'end_turn'},
            {'content': [{'type': 'text', 'text': 'more'}], 'stop_reason': 'end_turn'},
        ]
    elif protocol == 'gemini-interactions':
        bodies = [
            {
                'steps': [{'type': 'function_call', 'id': 't1', 'name': 'lookup', 'arguments': {}}],
                'status': 'completed',
            },
            {
                'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': 'answer'}]}],
                'status': 'completed',
            },
            {'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': 'more'}]}], 'status': 'completed'},
        ]
    else:
        bodies = [
            {'candidates': [{'content': {'role': 'model', 'parts': parts}, 'finishReason': 'STOP'}]}
            for parts in [
                [{'functionCall': {'id': 't1', 'name': 'lookup', 'args': {}}, 'thoughtSignature': 'signed-tool'}],
                [{'text': 'answer'}],
                [{'text': 'more'}],
            ]
        ]
    return {
        'id': 'reply',
        'role': 'assistant',
        'content': 'answer\nmore',
        'model': 'oi:leaf',
        'oi_native': [
            {'call_id': f'call-{i}', 'binding_id': 'oi:leaf', 'protocol': protocol, 'body': body, 'replayable': True}
            for i, body in enumerate(bodies)
        ],
        'output': [
            {'type': 'function_call', 'call_id': 't1', 'name': 'lookup', 'arguments': '{}', 'status': 'completed'},
            {'type': 'function_call_output', 'call_id': 't1', 'output': [{'type': 'input_text', 'text': 'result'}]},
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'answer\nmore'}]},
        ],
    }


@pytest.mark.parametrize('protocol', ['anthropic', 'gemini', 'cpa-agy', 'gemini-interactions'])
@pytest.mark.parametrize('portable', [False, True])
def test_portable_tool_history_with_continue_can_replay(protocol, portable):
    message = continued_tool_reply(protocol)
    if portable:
        message['oi_native'] = [replay_envelope(n) for n in message['oi_native']]
    payload, error = None, None
    try:
        messages = process_messages_with_output([message])
        _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert payload is not None, {
        'portable': portable,
        'protocol': protocol,
        'error': error,
        'native': message['oi_native'],
    }
    assert 'lookup' in json.dumps(payload)


@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('continued', [False, True])
def test_continue_keeps_generated_gemini_tool_ids(protocol, stream, continued):
    from open_webui.utils.oi.protocols import NativeStream

    message = continued_tool_reply(protocol)
    for native in message['oi_native']:
        native['protocol'] = protocol
    body = message['oi_native'][0]['body']
    body['candidates'][0]['content']['parts'][0]['functionCall'].pop('id')
    decoder = NativeStream(protocol)
    if stream:
        events = decoder.feed(copy.deepcopy(body))
        tools = [t for e in events for c in e.get('choices', []) for t in c.get('delta', {}).get('tool_calls', [])]
    else:
        tools = decoder.nonstream(copy.deepcopy(body))['choices'][0]['message']['tool_calls']
    assert len(tools) == 1
    original_id = tools[0]['id']
    message['oi_native'][0]['body'] = copy.deepcopy(decoder.body)
    message['output'][0]['call_id'] = original_id
    message['output'][1]['call_id'] = original_id
    if not continued:
        message['oi_native'].pop()
        message['content'] = 'answer'
        message['output'][-1]['content'][0]['text'] = 'answer'
    # No portable projection: this is the untouched conversation's stored envelope.
    assert 'finishReason' in message['oi_native'][0]['body']['candidates'][0]
    error = None
    try:
        messages = process_messages_with_output([message])
        _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert error is None, {
        'protocol': protocol,
        'stream': stream,
        'continued': continued,
        'original_tool_id': original_id,
        'error': error,
    }
    assert 'lookup' in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('temporary', [False, True])
async def test_generated_tool_mapping_survives_save_and_portable_replay(monkeypatch, protocol, stream, temporary):
    import httpx
    from open_webui.utils.oi.protocols import generate
    from open_webui.utils.oi.runtime import SSEDecoder
    from open_webui.utils.oi.temporary_native import restore

    message = continued_tool_reply(protocol)
    native = message['oi_native'][0]['body']
    native['candidates'][0]['content']['parts'][0]['functionCall'].pop('id')
    original = copy.deepcopy(native)
    meta = {'chat_id': 'local:socket' if temporary else 'saved', 'message_id': 'reply', 'oi_temporary_id': 'temp'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=meta))
    saved = AsyncMock()
    monkeypatch.setattr(Chats, 'get_message_by_id_and_message_id', AsyncMock(return_value={}))
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', saved)
    response = (
        httpx.Response(200, content='data: ' + json.dumps(native) + '\n\n')
        if stream
        else httpx.Response(200, json=native)
    )
    result = await generate(
        request,
        {'messages': [], 'metadata': meta, 'stream': stream},
        SimpleNamespace(id='u'),
        binding(protocol),
        connection(protocol),
        'call-0',
        httpx.MockTransport(lambda _: response),
    )
    if stream:
        decoder = SSEDecoder()
        events = []
        async for part in result.body_iterator:
            events.extend(decoder.feed(part.encode() if isinstance(part, str) else part))
        tools = [t for e in events for c in e.get('choices', []) for t in c.get('delta', {}).get('tool_calls', [])]
        capsule = next((e['oi_native_state'] for e in events if e.get('oi_native_state')), None)
    else:
        tools = result['choices'][0]['message']['tool_calls']
        capsule = result.get('oi_native_state')
    assert len(tools) == 1
    original_id = tools[0]['id']
    if temporary:
        saved.assert_not_called()
        api_message = {
            'id': 'reply',
            'role': 'assistant',
            'content': '',
            'tool_calls': [{k: v for k, v in t.items() if k != 'index'} for t in tools],
            'oi_native_state': capsule,
        }
        envelope = restore(api_message, 'u', 'temp')[0]
    else:
        envelope = saved.call_args.args[2]['oi_native'][0]
    envelope = json.loads(json.dumps(envelope))  # JSON converts sidecar keys to strings.
    assert envelope['body'] == original  # Signed provider blocks are never rewritten.
    assert list(envelope['generated_tool_ids'].values()) == [original_id]
    message['oi_native'][0] = envelope
    message['output'][0]['call_id'] = message['output'][1]['call_id'] = original_id
    for portable in (False, True):
        candidate = copy.deepcopy(message)
        if portable:
            candidate['oi_native'] = [replay_envelope(n) for n in candidate['oi_native']]
        messages = process_messages_with_output([candidate])
        _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
        signed = payload['contents'][0]['parts'][0]
        assert signed == original['candidates'][0]['content']['parts'][0]
        assert 'generated_tool_ids' not in json.dumps(payload)
        assert 'id' not in signed['functionCall']
        assert payload['contents'][1]['parts'][0]['functionResponse']['name'] == 'lookup'


@pytest.mark.parametrize('field,value', [('name', 'changed'), ('arguments', '{"changed":true}')])
def test_legacy_tool_id_recovery_rejects_changed_function(field, value):
    message = continued_tool_reply('gemini')
    message['oi_native'][0]['body']['candidates'][0]['content']['parts'][0]['functionCall'].pop('id')
    message['output'][0][field] = value
    with pytest.raises(ValueError, match='Native tool does not match'):
        process_messages_with_output([message])


@pytest.mark.parametrize(
    'protocol,reason',
    [('anthropic', 'max_tokens'), ('gemini', 'MAX_TOKENS'), ('cpa-agy', 'STOP'), ('gemini-interactions', 'failed')],
)
def test_portable_terminal_markers_do_not_enable_incomplete_tools(protocol, reason):
    from open_webui.utils.oi.replay import replay_message

    native = continued_tool_reply(protocol)['oi_native'][0]
    body = native['body']
    if protocol == 'anthropic':
        body['stop_reason'] = reason
    elif protocol == 'gemini-interactions':
        body['status'] = reason
    else:
        body['candidates'][0]['finishReason'] = reason
        if protocol == 'cpa-agy':
            body['candidates'][0]['cpaFinishReasonSource'] = 'synthetic-stop'
    assert 'tool_calls' not in replay_message(replay_envelope(native))


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['anthropic', 'gemini', 'cpa-agy', 'gemini-interactions'])
@pytest.mark.parametrize('transfer', ['fork', 'archive'])
async def test_tool_continue_after_real_fork_or_archive(db, monkeypatch, protocol, transfer):
    from open_webui.models.chats import ChatModel
    from open_webui.models.chat_messages import ChatMessages
    from open_webui.utils.oi import archive

    message = continued_tool_reply(protocol)
    message.update(parentId=None, childrenIds=[], user_id='owner', done=True)
    for native in message['oi_native']:
        native['body']['instructions'] = 'private-source-instructions'
    body = {'models': ['oi:leaf'], 'history': {'currentId': 'reply', 'messages': {'reply': message}}}
    row = Chat(
        id='source',
        user_id='owner',
        title='Tool history',
        chat=body,
        current_message_id='reply',
        created_at=1,
        updated_at=1,
        meta={},
    )
    db.add(row)
    await db.flush()
    await ChatMessages.upsert_messages('source', 'owner', {'reply': message}, db=db)
    await db.commit()
    if transfer == 'archive':
        manifest = await archive.export_records(db, 'owner', row.id, directory=[{'id': 'oi:leaf'}])
        result = await archive.restore_records(db, 'owner', manifest, 'restored', {'oi:leaf': 'oi:leaf'})
    else:
        monkeypatch.setattr(routes, 'require_chat_import_permission', AsyncMock())
        monkeypatch.setattr(routes, 'has_active_tasks', AsyncMock(return_value=False))
        monkeypatch.setattr(routes, 'publish_event', AsyncMock())
        monkeypatch.setattr(Chats, 'require_chat_creation_permission', AsyncMock())
        monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=ChatModel.model_validate(row)))
        monkeypatch.setattr(
            Chats, 'get_messages_map_by_chat_id', AsyncMock(return_value=copy.deepcopy(body['history']['messages']))
        )
        upsert = ChatMessages.upsert_message

        async def with_db(*args, **kwargs):
            kwargs['db'] = db
            return await upsert(*args, **kwargs)

        monkeypatch.setattr(ChatMessages, 'upsert_message', with_db)
        result = await routes.fork_chat_by_id(
            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))),
            row.id,
            routes.ForkForm(message_id='reply'),
            SimpleNamespace(id='owner', role='admin', name='Owner'),
            db,
        )
    assert 'private-source-instructions' not in json.dumps(result.chat)
    messages = process_messages_with_output([result.chat['history']['messages']['reply']])
    _, payload = build_request({'messages': messages}, binding(protocol), connection(protocol))
    assert 'lookup' in json.dumps(payload) and 'more' in json.dumps(payload)
    assert 'private-source-instructions' not in json.dumps(payload)
