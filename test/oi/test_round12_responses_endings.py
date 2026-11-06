"""Responses terminal status/usage through actual adapter, ledger and message consumer."""

import json
from contextlib import asynccontextmanager
import httpx
import pytest
from open_webui.models.customization import ModelCall
from open_webui.utils import middleware
from open_webui.utils.oi import protocols, runtime
from sqlalchemy import select
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize(
    'status,reason,error_present',
    [
        ('completed', None, False),
        ('incomplete', 'max_output_tokens', False),
        ('incomplete', 'content_filter', False),
        ('incomplete', 'unknown_limit', False),
        ('failed', None, True),
        ('failed', None, False),
    ],
)
@pytest.mark.parametrize('with_text', [False, True])
async def test_responses_terminal_state_reaches_message_and_ledger(
    db, monkeypatch, streaming, status, with_text, reason, error_present
):
    saved, events, ctx = context(monkeypatch, 'openai-responses')
    ctx['form_data']['stream'] = streaming

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    b, c = (binding('openai-responses'), connection('openai-responses'))
    message = {
        'type': 'message',
        'id': 'message-1',
        'role': 'assistant',
        'status': 'completed' if status == 'completed' else 'incomplete',
        'content': [{'type': 'output_text', 'text': 'partial-answer', 'annotations': []}],
    }
    body = {
        'id': 'response-1',
        'object': 'response',
        'model': 'synthetic-model',
        'status': status,
        'output': [message] if with_text else [],
        'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12},
    }
    if status == 'incomplete':
        body['incomplete_details'] = {'reason': reason}
    if status == 'failed' and error_present:
        body['error'] = {'code': 'server_error', 'message': 'Synthetic provider failure'}
    frames = []
    if with_text:
        frames += [
            {
                'type': 'response.output_item.added',
                'output_index': 0,
                'item': {**message, 'status': 'in_progress', 'content': []},
            },
            {
                'type': 'response.content_part.added',
                'item_id': message['id'],
                'output_index': 0,
                'content_index': 0,
                'part': {'type': 'output_text', 'text': '', 'annotations': []},
            },
            {
                'type': 'response.output_text.delta',
                'item_id': message['id'],
                'output_index': 0,
                'content_index': 0,
                'delta': 'partial-answer',
            },
            {'type': 'response.output_item.done', 'output_index': 0, 'item': message},
        ]
    frames.append({'type': 'response.' + status, 'response': body})
    transport = httpx.MockTransport(
        lambda _: (
            httpx.Response(200, content=''.join(('data: ' + json.dumps(f) + '\n\n' for f in frames)))
            if streaming
            else httpx.Response(200, json=body)
        )
    )

    async def dispatch(call_id):
        return await protocols.generate(ctx['request'], ctx['form_data'], ctx['user'], b, c, call_id, transport)

    response = await runtime.track_call(ctx['request'], ctx['form_data'], ctx['user'], dispatch, b, c)
    await middleware.process_chat_response(response, ctx)
    call = (await db.execute(select(ModelCall))).scalar_one()
    issues = []
    if call.status != status:
        issues.append({'ledger_status': call.status, 'expected': status})
    expected_finish = {'max_output_tokens': 'length', 'content_filter': 'content_filter'}.get(reason, 'unknown')
    if status == 'incomplete' and saved.get('finish_reason') != expected_finish:
        issues.append({'message_finish_reason': saved.get('finish_reason'), 'expected': expected_finish})
    if status == 'failed' and (not saved.get('error')):
        issues.append({'provider_error_missing': True})
    if True:
        usage = saved.get('usage') or {}
        if (
            usage.get('input_tokens', usage.get('prompt_tokens')) != 10
            or usage.get('output_tokens', usage.get('completion_tokens')) != 2
        ):
            issues.append({'message_usage': usage, 'expected_input': 10, 'expected_output': 2})
    assert call.usage.get('input_tokens') == 10 and call.usage.get('output_tokens') == 2
    assert saved.get('done'), (saved, events)
    if with_text:
        assert 'partial-answer' in json.dumps(saved['output'])
    assert not issues, issues
    assert saved['response_status'] == status
    assert saved.get('incomplete_details') == body.get('incomplete_details')
    assert call.usage['finish']['status'] == status
    assert saved['oi_native'][0]['body'] == body
    final = next(
        e['data'] for e in reversed(events) if e['type'] == 'chat:completion' and e.get('data', {}).get('done')
    )
    assert final['output'] == saved['output'] and final['response_status'] == status
