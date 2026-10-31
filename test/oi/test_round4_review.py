"""Fourth-round review: actual application functions, isolated synthetic records only."""

import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from open_webui.models.chats import Chat, Chats, ChatModel
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.customization import ModelBinding, ModelCall
from open_webui.routers import chats as chat_routes
from open_webui.utils.oi import accounting, capability_calls
from open_webui.utils.middleware import process_messages_with_output
from open_webui.utils.oi.protocols import build_request
from open_webui.utils.oi.protocols import NativeStream
from open_webui.utils import middleware
from test.oi.test_capability_calls import fixtures
from test.oi.test_review_regressions import saved_message, binding, connection


@pytest.mark.asyncio
async def test_chat_owner_cannot_read_collaborators_private_native_instructions(db):
    message = {
        'id': 'writer-reply',
        'role': 'assistant',
        'content': 'Shared visible answer',
        'user_id': 'writer',
        'oi_native': [
            {
                'call_id': 'writer-call',
                'binding_id': 'oi:writer-private',
                'protocol': 'openai-responses',
                'body': {
                    'instructions': 'writer-private-system-instructions',
                    'output': [
                        {
                            'type': 'message',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': 'Shared visible answer'}],
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
        chat={'history': {'currentId': 'writer-reply', 'messages': {'writer-reply': message}}},
        created_at=1,
        updated_at=1,
    )
    db.add(row)
    await db.commit()
    result = await chat_routes.shared_chat_response(row, SimpleNamespace(id='owner', role='user'), db=db)
    assert 'writer-private-system-instructions' not in json.dumps(result), result['chat']


@pytest.mark.asyncio
@pytest.mark.parametrize('actor', ['owner', 'writer'])
async def test_fork_retains_safe_native_replay(db, monkeypatch, actor):
    user = SimpleNamespace(id=actor, role='user', name='Writer')
    original = {
        **saved_message(),
        'id': 'reply',
        'parentId': 'question',
        'childrenIds': [],
        'user_id': 'owner',
        'done': True,
    }
    question = {
        'id': 'question',
        'role': 'user',
        'content': 'hello',
        'parentId': None,
        'childrenIds': ['reply'],
        'user_id': 'owner',
        'user': {'id': 'owner', 'name': 'Owner'},
    }
    body = {
        'models': ['oi:leaf'],
        'history': {'currentId': 'reply', 'messages': {'question': question, 'reply': original}},
    }
    row = Chat(
        id='shared-native',
        user_id='owner',
        title='Shared',
        chat=body,
        created_at=1,
        updated_at=1,
        meta={},
        current_message_id='reply',
    )
    db.add(row)
    await db.commit()
    model = ChatModel.model_validate(row)
    monkeypatch.setattr(chat_routes, 'require_chat_import_permission', AsyncMock())
    monkeypatch.setattr(chat_routes, 'has_active_tasks', AsyncMock(return_value=False))
    monkeypatch.setattr(chat_routes, 'publish_event', AsyncMock())
    monkeypatch.setattr(Chats, 'require_chat_creation_permission', AsyncMock())
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=model))
    monkeypatch.setattr(
        Chats, 'get_messages_map_by_chat_id', AsyncMock(return_value=copy.deepcopy(body['history']['messages']))
    )
    upsert = ChatMessages.upsert_message

    async def upsert_fixture(*args, **kwargs):
        kwargs['db'] = db
        return await upsert(*args, **kwargs)

    monkeypatch.setattr(ChatMessages, 'upsert_message', upsert_fixture)
    fork = await chat_routes.fork_chat_by_id(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))),
        row.id,
        chat_routes.ForkForm(message_id='reply'),
        user,
        db,
    )
    normalized = process_messages_with_output([fork.chat['history']['messages']['reply']])
    _, payload = build_request({'messages': normalized}, binding(), connection())
    assert 'synthetic-signature' in json.dumps(payload), payload


@pytest.mark.asyncio
@pytest.mark.parametrize('edit', [False, True])
async def test_image_capability_without_modality_detail_keeps_cost_unknown(db, monkeypatch, edit):
    user = await fixtures(db, monkeypatch, protocol='gemini')
    model = await db.get(ModelBinding, 'oi:a')
    model.pricing = {'rates': {'input': '1', 'output': '2'}}
    await db.commit()
    response = {
        'candidates': [
            {
                'content': {
                    'role': 'model',
                    'parts': [{'inlineData': {'mimeType': 'image/png', 'data': 'c3ludGhldGlj'}}],
                },
                'finishReason': 'STOP',
            }
        ],
        'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 100},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    options = {'input_images': ['data:image/png;base64,c3ludGhldGlj']} if edit else {}
    images, _ = await capability_calls.images(
        'oi:a', {'prompt': 'synthetic image'}, user, transport=transport, **options
    )
    assert images
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert call.estimated_usd is None, {'estimate': call.estimated_usd, 'usage': call.usage}


@pytest.mark.asyncio
@pytest.mark.parametrize('with_caption', [False, True])
async def test_image_only_native_reply_replays_in_next_turn(monkeypatch, with_caption):
    body = {
        'candidates': [
            {
                'content': {
                    'role': 'model',
                    'parts': [{'inlineData': {'mimeType': 'image/png', 'data': 'c3ludGhldGlj'}}],
                },
                'finishReason': 'STOP',
            }
        ],
        'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 100},
    }
    if with_caption:
        body['candidates'][0]['content']['parts'].append({'text': 'A caption'})
    response = NativeStream('gemini').nonstream(body)
    events = []

    async def emitter(event):
        events.append(event)

    monkeypatch.setattr(middleware, 'log_model_usage', lambda *a, **kw: None)
    monkeypatch.setattr(middleware, 'outlet_filter_handler', AsyncMock())
    monkeypatch.setattr(middleware, 'background_tasks_handler', AsyncMock())
    ctx = {
        'request': SimpleNamespace(state=SimpleNamespace()),
        'user': SimpleNamespace(id='u'),
        'metadata': {'chat_id': 'temporary:transport', 'message_id': 'reply'},
        'form_data': {'model': 'oi:leaf'},
        'events': [],
        'event_emitter': emitter,
    }
    await middleware.non_streaming_chat_response_handler(response, ctx)
    output = next(event['data']['output'] for event in reversed(events) if event.get('data', {}).get('done'))
    image_files = next(event['data']['files'] for event in events if event['type'] == 'chat:message:files')
    message = {
        'id': 'reply',
        'role': 'assistant',
        'content': 'A caption' if with_caption else '',
        'output': output,
        'model': 'oi:leaf',
        'files': image_files,
        'oi_native': [{'call_id': 'image-call', 'binding_id': 'oi:leaf', 'protocol': 'gemini', 'body': body}],
    }
    form = {'messages': process_messages_with_output([message, {'role': 'user', 'content': 'Describe that image'}])}
    _, payload = build_request(form, binding('gemini'), connection('gemini'))
    assert 'c3ludGhldGlj' in json.dumps(payload), {'events': events, 'payload': payload}


@pytest.mark.asyncio
async def test_outlet_rewrite_is_used_in_next_native_request(db, monkeypatch):
    original = {**saved_message(), 'id': 'reply', 'done': True, 'timestamp': 1, 'parentId': None, 'childrenIds': []}
    row = Chat(
        id='filtered',
        user_id='u',
        title='Filtered',
        chat={'history': {'currentId': 'reply', 'messages': {'reply': original}}},
        created_at=1,
        updated_at=1,
    )
    db.add(row)
    await db.flush()
    await ChatMessages.upsert_message('reply', row.id, 'u', original, db=db)

    @asynccontextmanager
    async def transaction(_id):
        yield db, row
        await db.flush()

    async def messages(_id):
        return await ChatMessages.get_messages_map_by_chat_id(_id, db=db)

    async def outlet(_request, data, _user, _models):
        data = copy.deepcopy(data)
        data['messages'][0]['content'] = 'filtered-answer'
        data['messages'][0]['output'][0]['content'][0]['text'] = 'filtered-answer'
        return data

    monkeypatch.setattr(Chats, '_chat_transaction', transaction)
    monkeypatch.setattr(Chats, 'get_messages_map_by_chat_id', messages)
    monkeypatch.setattr(middleware, 'get_filter_functions', AsyncMock(return_value=[]))
    monkeypatch.setattr(middleware, 'process_pipeline_outlet_filter', outlet)
    model = {'id': 'oi:leaf', 'pipeline': True}
    ctx = {
        'request': SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={'oi:leaf': model}))),
        'user': SimpleNamespace(id='u'),
        'metadata': {'chat_id': row.id, 'message_id': 'reply'},
        'model': model,
        'event_emitter': AsyncMock(),
        'event_caller': None,
        'assistant_message': original,
    }
    await middleware.outlet_filter_handler(ctx)
    saved = (await messages(row.id))['reply']
    assert saved['content'] == 'filtered-answer', saved
    _, payload = build_request({'messages': process_messages_with_output([saved])}, binding(), connection())
    replayed_text = ''.join(part.get('text', '') for message in payload['messages'] for part in message['content'])
    assert replayed_text == 'filtered-answer', {'stored_visible': saved['content'], 'provider_payload': payload}


@pytest.mark.asyncio
async def test_native_visibility_uses_call_actor_and_configuration_owner(db, monkeypatch):
    from open_webui.models.customization import Generation
    from open_webui.utils.oi.native_privacy import project_native

    await fixtures(db, monkeypatch, protocol='openai-responses')
    db.add(
        Generation(
            id='g',
            user_id='writer',
            chat_id='shared',
            message_id='m',
            request_id='request',
            kind='assistant',
            status='completed',
            created_at=1,
        )
    )
    db.add(
        ModelCall(
            id='call',
            generation_id='g',
            chat_id='shared',
            sequence=1,
            binding_id='oi:a',
            connection_id='a',
            model_id='oi:a',
            status='completed',
            created_at=1,
        )
    )
    await db.flush()
    original = {
        'history': {
            'messages': {
                'm': {
                    'id': 'm',
                    'role': 'assistant',
                    'user_id': 'u',
                    'oi_native': [
                        {
                            'call_id': 'call',
                            'binding_id': 'oi:a',
                            'protocol': 'openai-responses',
                            'body': {
                                'instructions': 'config-private',
                                'output': [
                                    {
                                        'type': 'message',
                                        'role': 'assistant',
                                        'content': [{'type': 'output_text', 'text': 'visible'}],
                                    }
                                ],
                            },
                        }
                    ],
                }
            }
        }
    }
    for viewer in ['u', 'writer', 'other', None]:
        result = await project_native(copy.deepcopy(original), 'u', viewer, db)
        assert 'config-private' not in json.dumps(result)
    from open_webui.models.customization import Connection

    await db.delete(await db.get(Connection, 'a'))
    await db.flush()
    removed = await project_native(copy.deepcopy(original), 'u', 'writer', db)
    assert 'config-private' not in json.dumps(removed)
    portable = await project_native(copy.deepcopy(original), 'u', 'u', db, portable=True)
    assert 'config-private' not in json.dumps(portable) and 'visible' in json.dumps(portable)
    assert 'config-private' in json.dumps(original)


@pytest.mark.asyncio
async def test_shared_native_archive_and_restore_keep_safe_replay_without_private_config(db, monkeypatch):
    from open_webui.utils.oi import archive

    native = {
        'call_id': 'source',
        'binding_id': 'oi:leaf',
        'protocol': 'anthropic',
        'body': {
            'instructions': 'writer-private',
            'content': [
                {'type': 'thinking', 'thinking': 'public thought', 'signature': 'safe-signature'},
                {'type': 'text', 'text': 'answer'},
            ],
        },
    }
    message = {**saved_message(), 'id': 'm', 'done': True, 'user_id': 'writer', 'oi_native': [native]}
    body = {
        'models': ['oi:leaf'],
        'history': {'currentId': 'm', 'messages': {'m': message}},
        'messages': [copy.deepcopy(message)],
    }
    db.add(Chat(id='archive-source', user_id='owner', title='Synthetic', chat=body, created_at=1, updated_at=1))
    await db.flush()
    await ChatMessages.upsert_message('m', 'archive-source', 'writer', message, db=db)
    manifest = await archive.export_records(db, 'owner', 'archive-source', directory=[{'id': 'oi:leaf'}])
    assert 'writer-private' not in json.dumps(manifest)
    assert 'safe-signature' in json.dumps(manifest)
    restored = await archive.restore_records(db, 'owner', manifest, 'restored', {'oi:leaf': 'oi:leaf'})
    stored = await db.get(Chat, 'restored')
    _, payload = build_request(
        {'messages': process_messages_with_output(list(stored.chat['history']['messages'].values()))},
        binding(),
        connection(),
    )
    assert 'safe-signature' in json.dumps(payload) and 'writer-private' not in json.dumps(payload)


@pytest.mark.asyncio
async def test_db_loading_keeps_native_image_without_canonical_output(db, monkeypatch):
    message = {
        'id': 'image',
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': '',
        'oi_native': [
            {
                'binding_id': 'oi:leaf',
                'protocol': 'gemini',
                'body': {
                    'candidates': [
                        {
                            'content': {
                                'role': 'model',
                                'parts': [
                                    {
                                        'inlineData': {'mimeType': 'image/png', 'data': 'aW1hZ2U='},
                                        'thoughtSignature': 'image-signature',
                                    }
                                ],
                            }
                        }
                    ]
                },
            }
        ],
    }
    monkeypatch.setattr(Chats, 'get_messages_map_by_chat_id', AsyncMock(return_value={'image': message}))
    chain = await middleware.load_messages_from_db('chat', 'image')
    assert chain and chain[0]['id'] == 'image'
    _, payload = build_request(
        {'messages': process_messages_with_output(chain)}, binding('gemini'), connection('gemini')
    )
    assert 'aW1hZ2U=' in json.dumps(payload) and 'image-signature' in json.dumps(payload)
    message['oi_native_invalidated'] = True
    assert await middleware.load_messages_from_db('chat', 'image') == []


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['content', 'output', 'none'])
async def test_temporary_outlet_patch_invalidates_only_changed_native_state(monkeypatch, changed):
    original = {**saved_message(), 'id': 'reply', 'oi_native_state': 'original-capsule', 'done': True}

    async def outlet(_request, data, _user, _models):
        data = copy.deepcopy(data)
        if changed == 'content':
            data['messages'][0]['content'] = 'filtered-text'
        if changed == 'output':
            data['messages'][0]['output'][0]['content'][0]['text'] = 'structured-only-edit'
        return data

    model = {'id': 'oi:leaf', 'pipeline': True}
    monkeypatch.setattr(middleware, 'get_filter_functions', AsyncMock(return_value=[]))
    monkeypatch.setattr(middleware, 'process_pipeline_outlet_filter', outlet)
    emitter = AsyncMock()
    await middleware.outlet_filter_handler(
        {
            'request': SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={'oi:leaf': model}))),
            'user': SimpleNamespace(id='u'),
            'metadata': {'chat_id': 'temporary:example', 'message_id': 'reply'},
            'model': model,
            'event_emitter': emitter,
            'event_caller': None,
            'assistant_message': original,
            'form_data': {'messages': []},
        }
    )
    patch = emitter.call_args.args[0]['data']['messages'][0]
    if changed == 'none':
        assert not patch.get('oi_native_invalidated')
    else:
        assert patch['oi_native_invalidated'] and patch['oi_native_state'] is None
        merged = {**original, **patch}
        _, payload = build_request({'messages': process_messages_with_output([merged])}, binding(), connection())
        assert ('filtered-text' if changed == 'content' else 'structured-only-edit') in json.dumps(payload)
        assert 'synthetic-signature' not in json.dumps(payload)


@pytest.mark.parametrize(
    'protocol,body,state',
    [
        ('openai-responses', {'output': [{'type': 'reasoning', 'encrypted_content': 'opaque'}]}, 'opaque'),
        (
            'gemini',
            {
                'candidates': [
                    {
                        'content': {
                            'role': 'model',
                            'parts': [
                                {'functionCall': {'name': 'lookup', 'args': {}}, 'thoughtSignature': 'signed-tool'}
                            ],
                        }
                    }
                ]
            },
            'signed-tool',
        ),
        (
            'openai-chat',
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'ok',
                            'provider_specific_fields': {
                                'reasoning_details': [{'data': 'encrypted'}],
                                'instructions': 'private-nested',
                            },
                        }
                    }
                ]
            },
            'encrypted',
        ),
    ],
)
def test_portable_projection_keeps_protocol_state_not_request_metadata(protocol, body, state):
    from open_webui.utils.oi.native_privacy import replay_envelope

    body = {**body, 'instructions': 'secret', 'metadata': {'request': 'private'}, 'previous_response_id': 'private-id'}
    projected = replay_envelope({'protocol': protocol, 'binding_id': 'oi:x', 'body': body})
    encoded = json.dumps(projected)
    assert state in encoded
    assert 'private' not in encoded and 'secret' not in encoded


def test_interactions_portable_steps_replay_without_system_instruction():
    from open_webui.utils.oi.native_privacy import replay_envelope

    native = replay_envelope(
        {
            'binding_id': 'oi:leaf',
            'protocol': 'gemini-interactions',
            'body': {
                'system_instruction': 'private system',
                'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': 'visible interaction'}]}],
            },
        }
    )
    _, payload = build_request(
        {'messages': [{'role': 'assistant', 'content': 'visible interaction', 'oi_native': native}]},
        binding('gemini-interactions'),
        connection('gemini-interactions'),
    )
    assert 'visible interaction' in json.dumps(payload) and 'private system' not in json.dumps(payload)
