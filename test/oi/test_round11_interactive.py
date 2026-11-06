"""Actual native consumer -> ask_user staging -> answer resolution -> next request."""

import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from open_webui.models.chats import Chats
from open_webui.utils import middleware, tool_approval
from open_webui.utils.ask_user import normalize_ask_user_request
from open_webui.utils.oi import protocols
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_round9_protocols import context
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('pre_normalized', [False, True])
@pytest.mark.parametrize('action', ['answer', 'reject', 'timeout'])
async def test_answering_native_ask_user_allows_model_continuation(monkeypatch, protocol, pre_normalized, action):
    arguments = {
        'questions': [
            {
                'id': 'route',
                'header': 'Route',
                'question': 'Choose a route.',
                'options': [
                    {'label': 'North', 'description': 'Go north.'},
                    {'label': 'South', 'description': 'Go south.'},
                ],
            }
        ]
    }
    if pre_normalized:
        arguments = normalize_ask_user_request(arguments)
    saved, events, ctx = context(monkeypatch, protocol)
    ctx['metadata']['tools'] = {
        'ask_user': {
            'type': 'builtin',
            'tool_id': 'builtin:ask_user',
            'spec': {
                'name': 'ask_user',
                'parameters': {'type': 'object', 'properties': {'questions': {'type': 'array'}}},
            },
        }
    }

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {k: rewrite(v) for k, v in value.items()}
        if value.get('name') == 'lookup':
            result['name'] = 'ask_user'
            for key in ['arguments', 'input', 'args']:
                if key in value:
                    result[key] = json.dumps(arguments) if isinstance(value[key], str) else copy.deepcopy(arguments)
        if protocol == 'ollama' and isinstance(result.get('tool_calls'), list):
            result['tool_calls'] = result['tool_calls'][:1]
        return result

    def wire(req):
        raw = provider_response(protocol, [], req)
        if protocol == 'ollama':
            return httpx.Response(200, content=json.dumps(rewrite(json.loads(raw.content))) + '\n')
        frames = [
            rewrite(json.loads(line[6:])) for line in raw.content.decode().splitlines() if line.startswith('data: ')
        ]
        return httpx.Response(200, content=''.join('data: ' + json.dumps(f) + '\n\n' for f in frames))

    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'ask-user-call',
        httpx.MockTransport(wire),
    )
    await middleware.process_chat_response(response, ctx)
    saved.update(role='assistant', model='oi:leaf', user_id='u')
    pending = next(i for i in saved['output'] if i.get('type') == 'function_call')
    assert pending['name'] == 'ask_user' and pending['status'] == 'pending', (saved, events)
    assert json.loads(pending['arguments']) == normalize_ask_user_request(arguments)

    @asynccontextmanager
    async def edit(*args):
        yield saved

    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=SimpleNamespace(user_id='u')))
    monkeypatch.setattr(Chats, 'edit_message_output', edit)
    monkeypatch.setattr(tool_approval, 'get_event_emitter', AsyncMock(return_value=None))
    form = tool_approval.ResolveToolCallForm(
        call_id=pending['call_id'],
        action='reject' if action == 'reject' else 'answer',
        answers={'route': 'North'} if action == 'answer' else None,
        timed_out=action == 'timeout',
    )
    before = copy.deepcopy(saved)
    with pytest.raises(HTTPException) as denied:
        await tool_approval.resolve_tool_call_output('synthetic-chat', 'reply', form, SimpleNamespace(id='other'))
    assert denied.value.status_code == 403 and saved == before
    resolved = await tool_approval.resolve_tool_call_output('synthetic-chat', 'reply', form, ctx['user'])
    with pytest.raises(HTTPException) as duplicate:
        await tool_approval.resolve_tool_call_output('synthetic-chat', 'reply', form, ctx['user'])
    assert duplicate.value.status_code == 409

    assert resolved['resume'] == 'model'
    error, payload = None, None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert error is None, error
    assert 'ask_user' in json.dumps(payload)
    assert ({'answer': 'answered', 'reject': 'rejected by user', 'timeout': 'timed_out'}[action]) in json.dumps(payload)
    pending['arguments'] = json.dumps({**json.loads(pending['arguments']), 'timeout_ms': 60000})
    with pytest.raises(ValueError, match='does not match the saved output'):
        middleware.process_messages_with_output([saved])
