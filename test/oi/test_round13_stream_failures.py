"""Failed/EOF streams must not dispatch buffered tools or publish success."""

import copy
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
import httpx
import pytest
from sqlalchemy import select
from open_webui.models.customization import ModelCall
from open_webui.utils import middleware
from open_webui.utils.oi import protocols, runtime
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection


def response_for(protocol, req, tools, ending):
    raw = provider_response(protocol, [] if tools else [{}, {}], req)
    if ending == 'success':
        return raw
    if protocol == 'ollama':
        body = json.loads(raw.content)
        body['done'] = False
        body.pop('done_reason', None)
        frames = [body]
    else:
        frames = [json.loads(line[6:]) for line in raw.content.decode().splitlines() if line.startswith('data: ')]
        if protocol == 'openai-chat':
            for f in frames:
                f['choices'][0]['finish_reason'] = None
        elif protocol == 'openai-responses':
            body = frames[-1]['response']
            frames = [
                {'type': 'response.output_item.done', 'output_index': i, 'item': {**item, 'status': 'completed'}}
                for i, item in enumerate(body['output'])
            ]
        elif protocol == 'anthropic':
            frames = [f for f in frames if f['type'] not in {'message_delta', 'message_stop'}]
        elif protocol == 'gemini-interactions':
            frames = [f for f in frames if f['event_type'] != 'interaction.completed']
        else:
            for f in frames:
                for candidate in f['candidates']:
                    candidate.pop('finishReason', None)
    if ending == 'error':
        frames.append({'error': {'message': 'synthetic interrupted provider', 'code': 'synthetic_error'}})
    return httpx.Response(
        200,
        content=''.join(
            ('' if protocol == 'ollama' else 'data: ') + json.dumps(f) + ('\n' if protocol == 'ollama' else '\n\n')
            for f in frames
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('ending', ['success', 'eof', 'error'])
@pytest.mark.parametrize('tools', [False, True])
async def test_failed_stream_never_executes_buffered_tools_or_reports_success(db, monkeypatch, protocol, ending, tools):
    saved, events, ctx = context(monkeypatch, protocol)
    executed, payloads = [], []

    async def lookup(q):
        executed.append(q)
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    b, c = binding(protocol), connection(protocol)
    success = middleware.publish_chat_finished_event
    followups = middleware.background_tasks_handler

    def wire(req):
        payloads.append(json.loads(req.content))
        return (
            response_for(protocol, req, tools, ending)
            if len(payloads) == 1
            else response_for(protocol, req, False, 'success')
        )

    async def generate(request, form, user, **kwargs):
        async def dispatch(call_id):
            return await protocols.generate(request, form, user, b, c, call_id, httpx.MockTransport(wire))

        return await runtime.track_call(request, form, user, dispatch, b, c)

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    calls = (await db.execute(select(ModelCall).order_by(ModelCall.sequence))).scalars().all()
    final = [e['data'] for e in events if e['type'] == 'chat:completion' and e.get('data', {}).get('done')]
    issues = []
    if ending != 'success':
        if executed:
            issues.append({'unexpected_tool_execution': executed})
        if len(payloads) != 1:
            issues.append({'unexpected_followup_requests': len(payloads)})
        if success.await_count or followups.await_count:
            issues.append({'success_notifications': success.await_count, 'background_followups': followups.await_count})
        if final and not final[-1].get('error'):
            issues.append({'terminal_error_missing': True})
        assert calls[0].status == 'failed'
        assert saved.get('error'), (saved, events)
    else:
        assert len(executed) == ((2 if protocol == 'ollama' else 1) if tools else 0)
        assert all(c.status == 'completed' for c in calls)
        assert success.await_count == 1
    if ending != 'success':
        from test.oi.test_round10_tools import pairs

        normalized = middleware.sanitize_tool_pairs(
            middleware.process_messages_with_output([{**saved, 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}])
        )
        _, outbound = protocols.build_request({'messages': normalized}, b, c)
        pending, outcomes = pairs(protocol, outbound)
        assert not pending and not outcomes
    assert not issues, issues


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('failure', ['http', 'exception'])
async def test_tool_followup_failure_does_not_publish_success(db, monkeypatch, protocol, failure):
    from test.oi.test_round10_tools import pairs

    saved, events, ctx = context(monkeypatch, protocol)
    payloads, executed = [], []

    async def lookup(q):
        executed.append(q)
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    b, c = binding(protocol), connection(protocol)

    def wire(req):
        payloads.append(json.loads(req.content))
        if len(payloads) == 1:
            return response_for(protocol, req, True, 'success')
        if failure == 'http':
            return httpx.Response(503, json={'error': {'message': 'synthetic followup error'}})
        raise httpx.ReadError('synthetic failed connection')

    async def generate(request, form, user, **kwargs):
        async def dispatch(cid):
            return await protocols.generate(request, form, user, b, c, cid, httpx.MockTransport(wire))

        return await runtime.track_call(request, form, user, dispatch, b, c)

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    assert len(payloads) == 2 and len(executed) == (2 if protocol == 'ollama' else 1)
    saved.update(role='assistant', model='oi:leaf')
    normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
    _, outbound = protocols.build_request({'messages': normalized}, b, c)
    calls, results = pairs(protocol, outbound)
    assert len(calls) == len(results) == len(executed)
    ledger = (await db.execute(select(ModelCall).order_by(ModelCall.sequence))).scalars().all()
    assert [call.status for call in ledger] == ['completed', 'failed']
    final = next(
        e['data'] for e in reversed(events) if e['type'] == 'chat:completion' and e.get('data', {}).get('done')
    )
    issues = []
    if middleware.publish_chat_finished_event.await_count or middleware.background_tasks_handler.await_count:
        issues.append(
            {
                'success_notifications': middleware.publish_chat_finished_event.await_count,
                'background_followups': middleware.background_tasks_handler.await_count,
            }
        )
    if not final.get('error'):
        issues.append({'terminal_error_missing': True})
    assert not issues, issues
