"""Widget-only metadata stays in embeds when completed tool postprocessing fails."""

import asyncio, copy, json
from unittest.mock import AsyncMock
import httpx, pytest
from open_webui.utils import middleware
from open_webui.utils.mcp.apps import MCPAppResult
from open_webui.utils.oi import protocols
from open_webui.utils.oi.temporary_native import restore
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection
from test.oi.test_native_tool_roundtrip import provider_response


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['openai-chat', 'anthropic', 'gemini', 'ollama'])
@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('temporary', [False, True])
async def test_widget_metadata_is_not_replayed_on_failed_processing(monkeypatch, protocol, cancel, temporary):
    saved, events, ctx = context(monkeypatch, protocol)
    if temporary:
        ctx['metadata'].update(chat_id='', oi_temporary_id='widget')

    async def tool(q):
        return MCPAppResult(
            {'type': 'mcp_app', 'result': {'_meta': {'private': 'widget-only-secret'}}},
            [{'type': 'text', 'text': 'public result'}],
        )

    ctx['metadata']['tools'] = {
        'lookup': {
            'type': 'mcp',
            'callable': tool,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    async def fail(*args, **kwargs):
        if cancel:
            raise asyncio.CancelledError()
        raise OSError('synthetic postprocessing failed')

    monkeypatch.setattr(middleware, 'process_tool_result', fail)
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'widget-call',
        httpx.MockTransport(lambda req: provider_response(protocol, [], req)),
    )
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await middleware.process_chat_response(response, ctx)
    else:
        await middleware.process_chat_response(response, ctx)
    if temporary:
        terminal = next(
            e['data'] for e in reversed(events) if e['type'] == 'chat:tasks:cancel' or e.get('data', {}).get('done')
        )
        saved = {**terminal, 'id': 'reply', 'role': 'assistant', 'model': 'oi:leaf'}
        assert restore(saved, 'u', 'widget')
    saved.update(role='assistant', model='oi:leaf')
    results = [item for item in saved['output'] if item.get('type') == 'function_call_output']
    assert results and results[0]['status'] == 'completed'
    assert 'widget-only-secret' in json.dumps(results[0]['embeds'])
    assert 'widget-only-secret' not in json.dumps(results[0]['output'])
    normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
    _, body = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
    assert 'public result' in json.dumps(body) and 'widget-only-secret' not in json.dumps(body)
