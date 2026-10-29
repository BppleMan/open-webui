"""Both transports share protocol adapters and ledger behavior, including interruption."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from open_webui.models.customization import ModelCall
from open_webui.utils.oi import runtime
from open_webui.utils.oi.browser_transport import BrowserTransport
from open_webui.utils.oi.protocols import generate, NativeStream
from open_webui.utils.oi.protocol_registry import COMMON_PROTOCOLS
from test.oi.test_review_regressions import binding, connection
from test.oi.test_native_tool_roundtrip import provider_response


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', COMMON_PROTOCOLS)
@pytest.mark.parametrize('kind', ['server', 'browser'])
@pytest.mark.parametrize('outcome', ['json', '404', '429', 'disconnect', 'cancel'])
async def test_native_transport_outcomes(db, monkeypatch, protocol, kind, outcome):
    from open_webui.socket import main as socket

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    monkeypatch.setattr('open_webui.models.config.Config.get', AsyncMock(return_value=True))
    monkeypatch.setattr(socket, 'SESSION_POOL', {'sid': {'id': 'u'}})
    monkeypatch.setattr(socket.sio, 'emit', AsyncMock())
    b, c, user = binding(protocol), connection(protocol), SimpleNamespace(id='u')
    c.transport, c.owner_id, c.enabled, c.revision = kind, 'u', True, 1
    metadata = {'session_id': 'sid', 'oi_temporary_id': 'stable-test'}
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    streaming = outcome != 'json'
    form = {
        'model': b.id,
        'messages': [{'role': 'user', 'content': 'synthetic'}],
        'stream': streaming,
        'metadata': metadata,
    }
    counts = []
    starts = {
        'openai-chat': {'choices': [{'delta': {'content': 'partial'}}], 'usage': {'prompt_tokens': 123}},
        'openai-responses': {
            'type': 'response.in_progress',
            'response': {'id': 'r', 'status': 'in_progress', 'usage': {'input_tokens': 123}},
        },
        'anthropic': {'type': 'message_start', 'message': {'content': [], 'usage': {'input_tokens': 123}}},
        'gemini': {
            'candidates': [{'content': {'parts': [{'text': 'partial thought', 'thought': True}]}}],
            'usageMetadata': {'promptTokenCount': 123},
        },
    }
    raw = ('data: ' + json.dumps(starts[protocol]) + '\n\n').encode()

    async def serve(req):
        counts.append(str(req.url))
        if protocol == 'gemini':
            assert json.loads(req.content)['generationConfig']['thinkingConfig']['includeThoughts'] is True
        if outcome in {'404', '429'}:
            return httpx.Response(int(outcome), json={'error': {'message': 'synthetic'}}, headers={'retry-after': '7'})
        if outcome == 'json':
            # Use signed native tool output, not a hand-written normalized completion.
            response = provider_response(protocol, [], req)
            state = NativeStream(protocol)
            for event in runtime.SSEDecoder().feed(response.content):
                state.feed(event)
            if protocol == 'openai-chat':
                state.body['usage'] = {'prompt_tokens': 10, 'completion_tokens': 3}
            return httpx.Response(200, json=state.body)

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield raw
                if outcome == 'cancel':
                    raise asyncio.CancelledError()
                raise httpx.ReadError('synthetic disconnect')

        return httpx.Response(200, stream=Body())

    transport = httpx.MockTransport(serve)
    if kind == 'browser':

        async def relay(event):
            data = event['data']
            assert data['stream'] == streaming and data['protocol'] == protocol
            if protocol == 'gemini':
                assert 'stream' not in data['form_data']
            response = await serve(httpx.Request('POST', data['url'], json=data['form_data']))
            if outcome in {'json', '404', '429'}:
                return {'status_code': response.status_code, 'headers': dict(response.headers), 'body': response.text}
            queue = socket.EVENT_QUEUES[data['channel']]
            await queue.put(raw)
            await queue.put({'error': True})
            return {'status_code': 200, 'headers': {'content-type': 'text/event-stream'}}

        monkeypatch.setattr(socket, 'get_event_call', AsyncMock(return_value=relay))
        transport = BrowserTransport(user, metadata, b, c)
    result = await runtime.track_call(
        request, form, user, lambda cid: generate(request, form, user, b, c, cid, transport), b, c
    )
    if outcome in {'404', '429'}:
        assert result.status_code == int(outcome)
        assert result.headers['retry-after'] == '7'
    elif outcome == 'json':
        encoded = json.dumps(result)
        assert 'lookup' in encoded and 'tool-1' in encoded
    else:
        try:
            iterator = result.body_iterator.__aiter__()
            first = await anext(iterator)
            while '123' not in first:
                first = await anext(iterator)
            if outcome == 'cancel' and kind == 'browser':
                # Stop after the already-confirmed usage reached the consumer.
                await iterator.athrow(asyncio.CancelledError())
            else:
                await anext(iterator)
        except (asyncio.CancelledError, httpx.ReadError, HTTPException):
            pass
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert len(counts) == 1
    if outcome == 'json':
        assert call.usage['input_tokens'] == 10 and call.usage['output_tokens'] == 3
        assert call.status == 'completed'
    elif outcome in {'disconnect', 'cancel'}:
        assert call.usage['input_tokens'] == 123
        assert call.status == ('cancelled' if outcome == 'cancel' else 'failed')
    else:
        assert call.status == 'failed'
    assert call.actual_usd is None and call.estimated_usd is None
    assert not socket.EVENT_QUEUES
