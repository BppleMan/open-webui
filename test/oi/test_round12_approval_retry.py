"""Approved tools interrupted after entry must not be executed again by retries."""

import asyncio
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
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('interrupted', [False, True])
async def test_approved_tool_retry_does_not_repeat_execution(monkeypatch, protocol, interrupted):
    saved, events, ctx = context(monkeypatch, protocol)
    executed = []

    async def lookup(q):
        executed.append(q)
        if interrupted:
            raise asyncio.CancelledError()
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }
    ctx['metadata']['params']['tool_approval_mode'] = 'ask'

    def wire(req):
        raw = provider_response(protocol, [], req)
        if protocol == 'ollama':
            body = json.loads(raw.content)
            body['message']['tool_calls'] = body['message']['tool_calls'][:1]
            return httpx.Response(200, content=json.dumps(body) + '\n')
        return raw

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
    call = next((i for i in saved['output'] if i.get('type') == 'function_call'))
    assert call['status'] == 'pending' and (not executed)

    @asynccontextmanager
    async def edit(*args):
        yield saved

    monkeypatch.setattr(Chats, 'edit_message_output', edit)
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=SimpleNamespace(user_id='u')))
    monkeypatch.setattr(tool_approval, 'get_event_emitter', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'get_event_emitter_and_caller', AsyncMock(return_value=(None, None)))
    form = tool_approval.ResolveToolCallForm(call_id=call['call_id'], action='approve')
    before = copy.deepcopy(saved)
    with pytest.raises(HTTPException) as denied:
        await tool_approval.resolve_tool_call_output('synthetic-chat', 'reply', form, SimpleNamespace(id='other-user'))
    assert denied.value.status_code == 403 and saved == before
    await tool_approval.resolve_tool_call_output('synthetic-chat', 'reply', form, ctx['user'])
    if interrupted:
        with pytest.raises(asyncio.CancelledError):
            await middleware.resume_tool_calls(
                ctx['request'], ctx['form_data'], ctx['user'], ctx['model'], ctx['metadata'], saved
            )
    else:
        await middleware.resume_tool_calls(
            ctx['request'], ctx['form_data'], ctx['user'], ctx['model'], ctx['metadata'], saved
        )
    assert len(executed) == 1
    await middleware.resume_tool_calls(
        ctx['request'], ctx['form_data'], ctx['user'], ctx['model'], ctx['metadata'], saved
    )
    assert len(executed) == 1
    with pytest.raises(HTTPException) as repeated:
        await tool_approval.resolve_tool_call_output('synthetic-chat', 'reply', form, ctx['user'])
    assert repeated.value.status_code == 409
    results = [i for i in saved['output'] if i.get('type') == 'function_call_output']
    assert len(results) == 1 and results[0]['status'] == ('incomplete' if interrupted else 'completed')
    if interrupted:
        assert 'outcome is unknown' in json.dumps(results[0])
    normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
    protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
