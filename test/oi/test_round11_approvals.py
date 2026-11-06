"""Native dispatch -> real saved approval/rejection -> actual guarded resume."""

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
from open_webui.utils.oi import protocols
from open_webui.utils.oi.native_privacy import replay_envelope
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_round9_protocols import context
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_review_regressions import binding, connection
from test.oi.test_round10_tools import pairs


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('action', ['approve', 'reject'])
async def test_native_tool_approval_keeps_dispatch_identity_and_executes_once(monkeypatch, protocol, action):
    saved, events, ctx = context(monkeypatch, protocol)
    executed = []

    async def lookup(q):
        executed.append(q)
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }
    ctx['metadata']['params']['tool_approval_mode'] = 'ask'

    def wire(req):
        response = provider_response(protocol, [], req)
        if protocol == 'ollama':
            body = json.loads(response.content)
            body['message']['tool_calls'] = body['message']['tool_calls'][:1]
            return httpx.Response(200, content=json.dumps(body) + '\n')
        return response

    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'approved-call',
        httpx.MockTransport(wire),
    )
    await middleware.process_chat_response(response, ctx)
    saved.update(role='assistant', model='oi:leaf', user_id='u')
    pending = next(i for i in saved['output'] if i.get('type') == 'function_call')
    assert pending['status'] == 'pending' and executed == []

    @asynccontextmanager
    async def edit(*args):
        yield saved

    monkeypatch.setattr(Chats, 'edit_message_output', edit)
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=SimpleNamespace(user_id='u')))
    monkeypatch.setattr(tool_approval, 'get_event_emitter', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_event_emitter_and_caller', AsyncMock(return_value=(None, None)))
    form = tool_approval.ResolveToolCallForm(call_id=pending['call_id'], action=action)
    resolved = await tool_approval.resolve_tool_call_output('synthetic-chat', 'reply', form, ctx['user'])
    assert resolved['resume'] == ('tools' if action == 'approve' else 'model')
    if action == 'approve':
        paused = await middleware.resume_tool_calls(
            ctx['request'], ctx['form_data'], ctx['user'], ctx['model'], ctx['metadata'], saved
        )
        assert paused is False and len(executed) == 1
        await middleware.resume_tool_calls(
            ctx['request'], ctx['form_data'], ctx['user'], ctx['model'], ctx['metadata'], saved
        )
        assert len(executed) == 1
    else:
        assert executed == []
    with pytest.raises(HTTPException) as error:
        await tool_approval.resolve_tool_call_output('synthetic-chat', 'reply', form, ctx['user'])
    assert error.value.status_code == 409
    for portable in [False, True]:
        candidate = copy.deepcopy(saved)
        if portable:
            candidate['oi_native'] = [replay_envelope(n) for n in candidate['oi_native']]
        messages = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([candidate]))
        payload = protocols.build_request({'messages': messages}, binding(protocol), connection(protocol))[1]
        calls, results = pairs(protocol, payload)
        assert len(calls) == len(results) == 1
        if protocol != 'ollama':
            assert calls[0][0] == results[0]
