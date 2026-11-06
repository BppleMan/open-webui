"""Synthetic installed stream filter through the actual filter engine and consumer."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.utils import middleware, filter as filters
from open_webui.utils.oi import protocols
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection
from test.oi.round10_helpers import PROTOCOLS, text_response


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('changed', [False, True])
@pytest.mark.parametrize('temporary', [False, True])
async def test_stream_filter_content_remains_authoritative(monkeypatch, protocol, changed, temporary):
    before, after = 'synthetic-original-secret', 'synthetic-filtered-text'
    saved, events, ctx = context(monkeypatch, protocol)
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='synthetic-temporary')

    async def transform(event):
        if not changed:
            return event
        return json.loads(json.dumps(event).replace(before, after))

    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(filters, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(
        middleware, 'get_filter_functions', AsyncMock(return_value=[SimpleNamespace(id='synthetic-stream-filter')])
    )
    monkeypatch.setattr(filters, 'get_function_module', AsyncMock(return_value=SimpleNamespace(stream=transform)))
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'filtered-call',
        httpx.MockTransport(lambda _: text_response(protocol, [before])),
    )
    await middleware.process_chat_response(response, ctx)
    if temporary:
        from open_webui.utils.oi.temporary_native import restore

        final = next(event['data'] for event in reversed(events) if event.get('data', {}).get('done'))
        saved = {**final, 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        saved['oi_native'] = restore(saved, ctx['user'].id, 'synthetic-temporary')
    else:
        saved.update(id='reply', role='assistant', model='oi:leaf')
    expected = after if changed else before
    assert expected in json.dumps(saved.get('output')), {'saved': saved, 'events': events}
    original = copy.deepcopy(saved)
    error, payload = None, None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = str(exc)
    assert saved == original
    assert error is None, error
    assert expected in json.dumps(payload), payload
    if changed:
        assert before not in json.dumps(payload), payload
