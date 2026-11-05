"""Independent complete-chain review probes; MockTransport and local fixtures only."""

import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.models.chats import Chats
from open_webui.utils import chat, middleware
from open_webui.utils.middleware import process_messages_with_output, sanitize_tool_pairs
from open_webui.utils.oi import legacy, model_capabilities, model_targets, protocols, runtime
from open_webui.utils.oi.protocols import build_request

from test.oi.test_review_regressions import binding, connection
from test.oi.test_round8_review import gemini_body, text_item, tool_items


def context(monkeypatch, protocol, initial=None, **extra_metadata):
    saved, events = copy.deepcopy(initial or {}), []

    async def capture(cid, mid, update, **kwargs):
        saved.update(copy.deepcopy(update))

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
    model = {
        'id': 'oi:leaf',
        'owned_by': 'openai',
        'oi_binding': True,
        'connection': {'protocol': protocol, 'transport': 'server'},
    }
    metadata = {'chat_id': 'synthetic-chat', 'message_id': 'reply', 'params': {}, **extra_metadata}
    request = SimpleNamespace(
        state=SimpleNamespace(metadata=metadata, max_tool_call_iterations=3),
        app=SimpleNamespace(state=SimpleNamespace(redis=None, MODELS={'oi:leaf': model})),
    )
    user = SimpleNamespace(id='u', role='admin')
    form = {'model': 'oi:leaf', 'messages': [{'role': 'user', 'content': 'Next'}], 'metadata': metadata, 'stream': True}
    ctx = {
        'request': request,
        'form_data': form,
        'user': user,
        'model': model,
        'metadata': metadata,
        'events': [],
        'event_emitter': emit,
        'event_caller': None,
        'assistant_message': copy.deepcopy(initial),
        'tasks': {},
    }
    return saved, events, ctx


def wire_response(protocol, body):
    return httpx.Response(
        200, content=json.dumps(body) + '\n' if protocol == 'ollama' else 'data: ' + json.dumps(body) + '\n\n'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy', 'ollama'])
@pytest.mark.parametrize('mode', ['plain', 'tags_disabled', 'tags_default'])
async def test_reasoning_tag_parsing_remains_valid_history(monkeypatch, protocol, mode):
    raw_text = 'answer' if mode == 'plain' else '<think>synthetic reasoning</think>answer'
    body = (
        {'message': {'role': 'assistant', 'content': raw_text}, 'done': True}
        if protocol == 'ollama'
        else gemini_body(raw_text)
    )
    saved, events, ctx = context(
        monkeypatch, protocol, params={'reasoning_tags': False} if mode == 'tags_disabled' else {}
    )
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'native-first',
        httpx.MockTransport(lambda _: wire_response(protocol, body)),
    )
    await middleware.process_chat_response(response, ctx)
    saved.update(role='assistant', model='oi:leaf')
    assert saved.get('done') and saved.get('output'), {'events': events, 'saved': saved}
    assert any(i['type'] == 'reasoning' for i in saved['output']) is (mode == 'tags_default')
    assert saved['oi_native'][0]['body'] == body
    original = copy.deepcopy(saved)
    error, payload = None, None
    try:
        normalized = sanitize_tool_pairs(process_messages_with_output([saved]))
        _, payload = build_request({'messages': normalized}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert saved == original
    assert error is None, error
    assert 'answer' in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('route', ['direct', 'arena_selected', 'arena_fallback'])
@pytest.mark.parametrize('temporary', [False, True])
async def test_arena_keeps_live_native_identity_through_real_tracking(db, monkeypatch, protocol, route, temporary):
    old_tool = {'id': 't1', 'function': {'name': 'lookup', 'arguments': '{"q":"first"}'}}
    initial = {
        'role': 'assistant',
        'model': 'oi:leaf',
        'output': [text_item('prefix'), *tool_items(old_tool)],
        'oi_native': [
            {
                'call_id': 'old-call',
                'binding_id': 'oi:leaf',
                'protocol': protocol,
                'replayable': True,
                'body': gemini_body('prefix', {'id': 't1', 'name': 'lookup', 'args': {'q': 'first'}}),
            }
        ],
    }
    saved, events, ctx = context(monkeypatch, protocol, initial, assistant_message_id='reply')
    request, user, form, metadata = ctx['request'], ctx['user'], ctx['form_data'], ctx['metadata']
    if temporary:
        from open_webui.utils.oi.temporary_native import restore_request, seal, wrap_emitter

        metadata.update(chat_id='', oi_temporary_id='temporary-arena')
        previous = {
            **initial,
            'id': 'reply',
            'oi_native_state': seal(initial['oi_native'], initial, user.id, 'temporary-arena', 'reply'),
        }
        restore_request(request, [previous], metadata, user)
        ctx['event_emitter'] = wrap_emitter(ctx['event_emitter'], request, metadata, user)
    tool_inputs, payloads = [], []

    async def lookup(q: str):
        tool_inputs.append(q)
        return 'synthetic-result:' + q

    metadata['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(chat, 'get_async_db', session)
    monkeypatch.setattr(runtime, 'get_async_db', session)
    monkeypatch.setattr(legacy, 'alias_map', AsyncMock(return_value={}))
    monkeypatch.setattr(chat, 'check_model_access', AsyncMock())
    b, c = binding(protocol), connection(protocol)
    c.transport = 'server'
    c.owner_id = 'u'

    async def target(db, reference, user):
        return SimpleNamespace(binding=b, connection=c, params={}, preset_ids=[]) if reference == 'oi:leaf' else None

    monkeypatch.setattr(model_targets, 'resolve_native_target', target)
    monkeypatch.setattr(model_capabilities, 'accessible_arena_candidates', AsyncMock(return_value=['oi:leaf']))
    arena = {'id': 'arena', 'owned_by': 'arena', 'info': {'meta': {'model_ids': ['oi:leaf']}}}
    request.app.state.MODELS['arena'] = arena
    await runtime.start_generation(user, metadata, 'oi:leaf')
    if route == 'arena_selected':
        metadata['selected_model_id'] = 'oi:leaf'
    elif route == 'arena_fallback':
        form['model'] = 'arena'
    real_generate = protocols.generate

    def provider(req):
        payloads.append(json.loads(req.content))
        body = (
            gemini_body(tool={'id': 't1', 'name': 'lookup', 'args': {'q': 'second'}})
            if len(payloads) == 1
            else gemini_body('answer')
        )
        return wire_response(protocol, body)

    async def adapter(request, form, user, binding, connection, call_id, **kwargs):
        return await real_generate(request, form, user, binding, connection, call_id, httpx.MockTransport(provider))

    monkeypatch.setattr(protocols, 'generate', adapter)
    # Both initial dispatch and follow-up use the real model router and real ledger wrapper.
    monkeypatch.setattr(middleware, 'generate_chat_completion', chat.generate_chat_completion)
    response = await chat.generate_chat_completion(request, form, user)
    initial_header = response.headers.get('x-oi-native-call-id')
    await middleware.process_chat_response(response, ctx)
    if temporary:
        from open_webui.utils.oi.temporary_native import restore

        final = next(event['data'] for event in reversed(events) if event.get('data', {}).get('done'))
        saved = {**final, 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        assert len(restore(saved, user.id, 'temporary-arena')) == 3
    assert tool_inputs == ['second'], {'events': events, 'saved': saved}
    assert len(payloads) == 2
    parts = [p for item in payloads[-1]['contents'] for p in item['parts']]
    functions = [p for p in parts if 'functionCall' in p]
    results = [p for p in parts if 'functionResponse' in p]
    assert len(results) == 1
    assert len(functions) == 1, {'header': initial_header, 'payload': payloads[-1]}
    normalized = sanitize_tool_pairs(process_messages_with_output([saved]))
    build_request({'messages': normalized}, b, c)


@pytest.mark.parametrize(
    'protocol', ['anthropic', 'openai-responses', 'gemini-interactions', 'gemini', 'cpa-agy', 'openai-chat', 'ollama']
)
@pytest.mark.parametrize('continued', [False, True])
@pytest.mark.parametrize('portable', [False, True])
def test_signed_tool_boundary_keeps_original_order(protocol, continued, portable):
    from open_webui.utils.oi.native_privacy import replay_envelope

    signature = 'signature-before-tool'
    function = {'name': 'lookup', 'arguments': '{}'}
    tool = {'id': 't1', 'type': 'function', 'function': function}
    reasoning_item = {'type': 'reasoning', 'summary': [{'type': 'output_text', 'text': 'reason'}]}
    if protocol == 'anthropic':
        first = {
            'content': [
                {'type': 'thinking', 'thinking': 'reason', 'signature': signature},
                {'type': 'tool_use', 'id': 't1', 'name': 'lookup', 'input': {}},
            ],
            'stop_reason': 'tool_use',
        }

        def body(text):
            return {'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn'}
    elif protocol == 'openai-responses':
        first = {
            'status': 'completed',
            'output': [
                {'type': 'reasoning', 'id': 'r1', 'summary': [], 'encrypted_content': signature},
                {'type': 'function_call', 'id': 'fc1', 'call_id': 't1', 'name': 'lookup', 'arguments': '{}'},
            ],
        }

        def body(text):
            return {
                'status': 'completed',
                'output': [
                    {
                        'type': 'message',
                        'id': 'm' + text,
                        'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': text}],
                    }
                ],
            }
    elif protocol == 'gemini-interactions':
        first = {
            'status': 'completed',
            'steps': [
                {'type': 'thought', 'signature': signature, 'summary': [{'type': 'text', 'text': 'reason'}]},
                {'type': 'function_call', 'id': 't1', 'name': 'lookup', 'arguments': {}},
            ],
        }

        def body(text):
            return {
                'status': 'completed',
                'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': text}]}],
            }
    elif protocol in {'gemini', 'cpa-agy'}:
        first = gemini_body(tool={'id': 't1', 'name': 'lookup', 'args': {}})
        first['candidates'][0]['content']['parts'][0]['thoughtSignature'] = signature
        body = gemini_body
    elif protocol == 'openai-chat':
        details = [{'type': 'reasoning.encrypted', 'id': 'r1', 'data': signature}]
        first = {
            'choices': [
                {
                    'message': {'role': 'assistant', 'content': '', 'tool_calls': [tool], 'reasoning_details': details},
                    'finish_reason': 'tool_calls',
                }
            ]
        }
        reasoning_item['reasoning_details'] = details

        def body(text):
            return {'choices': [{'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}]}
    else:
        first = {
            'message': {
                'role': 'assistant',
                'content': '',
                'thinking': signature,
                'tool_calls': [{'id': 't1', 'function': {'name': 'lookup', 'arguments': {}}}],
            },
            'done': True,
        }

        def body(text):
            return {'message': {'role': 'assistant', 'content': text}, 'done': True}

    bodies = [first, body('answer')] + ([body('more')] if continued else [])
    saved = {
        'role': 'assistant',
        'model': 'oi:leaf',
        'output': [reasoning_item, *tool_items(tool), text_item('answer\nmore' if continued else 'answer')],
        'oi_native': [
            {'call_id': str(i), 'binding_id': 'oi:leaf', 'protocol': protocol, 'body': b, 'replayable': True}
            for i, b in enumerate(bodies)
        ],
    }
    if portable:
        saved['oi_native'] = [replay_envelope(n) for n in saved['oi_native']]
    original = copy.deepcopy(saved)
    normalized = sanitize_tool_pairs(process_messages_with_output([saved]))
    _, payload = build_request({'messages': normalized}, binding(protocol), connection(protocol))
    encoded = json.dumps(payload)
    # The signature/reasoning block belongs before this tool's invocation and result.
    assert saved == original
    assert signature in encoded and encoded.index(signature) < encoded.index('synthetic-result'), payload

    def dictionaries(value):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from dictionaries(item)
        elif isinstance(value, list):
            for item in value:
                yield from dictionaries(item)

    signed_blocks = [
        block
        for block in dictionaries(first)
        if any(block.get(k) == signature for k in ('signature', 'thoughtSignature', 'encrypted_content', 'data'))
    ]
    assert all(block in list(dictionaries(payload)) for block in signed_blocks)
    assert encoded.index('synthetic-result') < encoded.index('answer')
    if continued:
        assert encoded.index('answer') < encoded.index('more')


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy', 'ollama'])
@pytest.mark.parametrize(
    'style', ['split', 'attributes', 'custom', 'empty', 'solution', 'multiple', 'mixed_chunks', 'unclosed']
)
async def test_tag_projection_through_real_consumer_continue_and_edit(monkeypatch, protocol, style):
    styles = {
        'split': ['<thi', 'nk>reason', '</think>', 'answer'],
        'attributes': ['<think mood="quiet">reason</think>answer'],
        'custom': ['<analysis>reason</analysis>answer'],
        'empty': ['<think></think>answer'],
        'solution': ['<|begin_of_solution|>answer<|end_of_solution|>'],
        'multiple': ['preface <think>reason</think>between', '<think>second</think>answer'],
        'mixed_chunks': [
            '<think>one</think>a<think>unparsed two</think>b',
            '<think>three</think>c<think>unparsed four</think>answer',
        ],
        'unclosed': ['<think>unfinished reason'],
    }
    pieces = styles[style]
    params = {'reasoning_tags': ['<analysis>', '</analysis>']} if style == 'custom' else {}
    saved, events, ctx = context(monkeypatch, protocol, params=params)

    def wire(req):
        frames = []
        for index, piece in enumerate(pieces):
            last = index == len(pieces) - 1
            body = (
                {'message': {'role': 'assistant', 'content': piece}, 'done': last}
                if protocol == 'ollama'
                else gemini_body(piece, finish='STOP' if last else None)
            )
            frames.append((json.dumps(body) + '\n') if protocol == 'ollama' else ('data: ' + json.dumps(body) + '\n\n'))
        return httpx.Response(200, content=''.join(frames))

    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'first',
        httpx.MockTransport(wire),
    )
    await middleware.process_chat_response(response, ctx)
    saved.update(role='assistant', model='oi:leaf')
    original = copy.deepcopy(saved)
    for portable in [False, True]:
        candidate = copy.deepcopy(saved)
        if portable:
            from open_webui.utils.oi.native_privacy import replay_envelope

            candidate['oi_native'] = [replay_envelope(n) for n in candidate['oi_native']]
        _, payload = build_request(
            {'messages': sanitize_tool_pairs(process_messages_with_output([candidate]))},
            binding(protocol),
            connection(protocol),
        )
        assert json.dumps(''.join(pieces), ensure_ascii=False) in json.dumps(payload, ensure_ascii=False)
    # A real Continue is another consumer invocation on the same display reply.
    continued, _, next_ctx = context(monkeypatch, protocol, saved, assistant_message_id='reply', params=params)
    body = (
        {'message': {'role': 'assistant', 'content': 'more'}, 'done': True}
        if protocol == 'ollama'
        else gemini_body('more')
    )
    response = await protocols.generate(
        next_ctx['request'],
        next_ctx['form_data'],
        next_ctx['user'],
        binding(protocol),
        connection(protocol),
        'continued',
        httpx.MockTransport(lambda _: wire_response(protocol, body)),
    )
    await middleware.process_chat_response(response, next_ctx)
    normalized = sanitize_tool_pairs(process_messages_with_output([continued]))
    _, payload = build_request({'messages': normalized}, binding(protocol), connection(protocol))
    assert 'more' in json.dumps(payload)
    assert saved == original
    from open_webui.utils.oi.replay import reconcile_native_edit

    edited = copy.deepcopy(continued)
    edited['id'] = 'reply'
    edited['output'] = [text_item('edited visible answer')]
    reconcile_native_edit(edited, {'reply': continued})
    assert edited['oi_native_invalidated']
    _, payload = build_request(
        {'messages': process_messages_with_output([edited])}, binding(protocol), connection(protocol)
    )
    assert 'edited visible answer' in json.dumps(payload)
    assert 'unfinished reason' not in json.dumps(payload) and '<think' not in json.dumps(payload)


def test_tag_matching_rejects_changed_recorded_reasoning():
    raw = '<think>original reason</think>answer'
    saved = {
        'role': 'assistant',
        'oi_native': [{'call_id': 'first', 'binding_id': 'oi:leaf', 'protocol': 'gemini', 'body': gemini_body(raw)}],
        'output': [
            {
                'type': 'reasoning',
                'start_tag': '<think>',
                'end_tag': '</think>',
                'content': [{'type': 'output_text', 'text': 'changed reason'}],
            },
            text_item('answer'),
        ],
    }
    with pytest.raises(ValueError, match='Native continuation does not match'):
        process_messages_with_output([saved])


@pytest.mark.parametrize('cancelled_tool', [False, True])
def test_opaque_only_reasoning_survives_continue_without_crossing_a_tool_result(cancelled_tool):
    reasoning = {'type': 'reasoning', 'id': 'r', 'summary': [], 'encrypted_content': 'opaque-retained'}
    first = {'output': [reasoning], 'status': 'completed'}
    output = []
    if cancelled_tool:
        first['output'].append(
            {'type': 'function_call', 'id': 'fc', 'call_id': 't', 'name': 'lookup', 'arguments': '{}'}
        )
        output.append(
            {'type': 'function_call', 'call_id': 't', 'status': 'incomplete', 'name': 'lookup', 'arguments': '{}'}
        )
    answer = {'status': 'completed', 'output': [text_item('answer')]}
    saved = {
        'role': 'assistant',
        'output': output + [text_item('answer')],
        'oi_native': [
            {'call_id': str(i), 'binding_id': 'oi:leaf', 'protocol': 'openai-responses', 'body': body}
            for i, body in enumerate([first, answer])
        ],
    }
    _, payload = build_request(
        {'messages': sanitize_tool_pairs(process_messages_with_output([saved]))},
        binding('openai-responses'),
        connection('openai-responses'),
    )
    encoded = json.dumps(payload)
    assert 'opaque-retained' in encoded and encoded.index('opaque-retained') < encoded.index('answer')
    assert 'lookup' not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize('edited', [False, True])
async def test_generated_file_and_native_inline_alias_are_sent_once_after_hydration(monkeypatch, edited):
    url = 'data:image/png;base64,eA=='
    saved = {
        'role': 'assistant',
        'files': [{'type': 'image', 'url': '/api/v1/files/synthetic/content'}],
        'content': 'caption' if edited else '',
        'oi_native_invalidated': edited,
        'oi_native': [
            {
                'call_id': 'image',
                'binding_id': 'oi:source',
                'protocol': 'gemini',
                'body': {
                    'candidates': [
                        {
                            'content': {
                                'role': 'model',
                                'parts': [{'inlineData': {'mimeType': 'image/png', 'data': 'eA=='}}],
                            },
                            'finishReason': 'STOP',
                        }
                    ]
                },
            }
        ],
    }
    before = copy.deepcopy(saved)
    resolver = AsyncMock(return_value=url)
    monkeypatch.setattr(middleware, 'get_image_base64_from_url', resolver)
    user = SimpleNamespace(id='owner')
    form = await middleware.convert_url_images_to_base64({'messages': process_messages_with_output([saved])}, user=user)
    _, body = build_request(form, binding('gemini'), connection('gemini'))
    assert sum('inlineData' in p for c in body['contents'] for p in c['parts']) == 1
    resolver.assert_awaited_once_with('/api/v1/files/synthetic/content', user=user)
    assert saved == before
    if edited:
        assert 'caption' in json.dumps(body)


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'assistant'])
async def test_hydrated_media_dedup_keeps_distinct_images_and_user_multiplicity(monkeypatch, role):
    first = 'data:image/png;base64,eA=='
    second = 'data:image/png;base64,eQ=='
    monkeypatch.setattr(middleware, 'get_image_base64_from_url', AsyncMock(return_value=first))
    form = {
        'messages': [
            {
                'role': role,
                'content': [
                    {'type': 'image_url', 'image_url': {'url': '/api/v1/files/synthetic/content', 'detail': 'high'}},
                    {'type': 'image_url', 'image_url': {'url': first}},
                    {'type': 'text', 'text': 'caption'},
                    {'type': 'image_url', 'image_url': {'url': second}},
                ],
            }
        ]
    }
    result = await middleware.convert_url_images_to_base64(form, user=SimpleNamespace(id='owner'))
    parts = result['messages'][0]['content']
    assert len(parts) == (3 if role == 'assistant' else 4)
    assert parts[0]['image_url']['detail'] == 'high'
    assert parts[-1]['image_url']['url'] == second
    assert {'type': 'text', 'text': 'caption'} in parts
