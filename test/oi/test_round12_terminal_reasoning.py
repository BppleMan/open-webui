"""Executed tools followed by text, empty, or reasoning-only terminal responses."""

import copy
import json
import httpx
import pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_native_tool_roundtrip import provider_response
from test.oi.test_round9_protocols import context
from test.oi.test_round10_tools import pairs
from test.oi.test_review_regressions import binding, connection


def terminal_response(protocol, req, mode, limited):
    raw = provider_response(protocol, [{}, {}], req)
    if protocol == 'ollama':
        body = json.loads(raw.content)
        if mode != 'text':
            body['message']['content'] = ''
        body['message']['thinking'] = '' if mode == 'empty' else 'terminal-thought'
        body['done_reason'] = 'length' if limited else 'stop'
        return httpx.Response(200, content=json.dumps(body) + '\n')
    frames = [json.loads(line[6:]) for line in raw.content.decode().splitlines() if line.startswith('data: ')]
    if protocol == 'openai-chat':
        for frame in frames:
            choice = frame['choices'][0]
            if mode != 'text':
                choice['delta']['content'] = ''
            if mode == 'empty':
                choice['delta'].pop('reasoning_content', None)
            choice['finish_reason'] = 'length' if limited else 'stop'
    elif protocol == 'openai-responses':
        for frame in frames:
            body = frame['response']
            if mode != 'text':
                body['output'] = [x for x in body['output'] if mode == 'reasoning_only' and x['type'] == 'reasoning']
            if limited:
                frame['type'] = 'response.incomplete'
                body.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
        terminal = frames[-1]
        frames = [
            {'type': 'response.output_item.done', 'output_index': i, 'item': copy.deepcopy(item)}
            for i, item in enumerate(terminal['response']['output'])
        ] + frames
    elif protocol in {'gemini', 'cpa-agy'}:
        for frame in frames:
            candidate = frame['candidates'][0]
            if mode != 'text':
                candidate['content']['parts'] = [
                    x for x in candidate['content']['parts'] if mode == 'reasoning_only' and x.get('thought')
                ]
            candidate['finishReason'] = 'MAX_TOKENS' if limited else 'STOP'
    elif protocol == 'anthropic':
        removed = {
            f['index']
            for f in frames
            if f['type'] == 'content_block_start'
            and (mode == 'empty' or (mode == 'reasoning_only' and f['content_block']['type'] != 'thinking'))
        }
        frames = [f for f in frames if f.get('index') not in removed]
        for frame in frames:
            if frame['type'] == 'message_delta':
                frame['delta']['stop_reason'] = 'max_tokens' if limited else 'end_turn'
    else:
        removed = {
            f['index']
            for f in frames
            if f['event_type'] == 'step.start'
            and (mode == 'empty' or (mode == 'reasoning_only' and f['step']['type'] != 'thought'))
        }
        frames = [f for f in frames if f.get('index') not in removed]
        for frame in frames:
            if (
                frame['event_type'] == 'step.start'
                and frame['step']['type'] == 'thought'
                and (mode == 'reasoning_only')
            ):
                frame['step']['summary'] = [{'type': 'text', 'text': 'terminal-thought'}]
            if frame['event_type'] == 'interaction.completed':
                frame['interaction']['status'] = 'incomplete' if limited else 'completed'
    return httpx.Response(200, content=''.join(('data: ' + json.dumps(f) + '\n\n' for f in frames)))


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('mode', ['text', 'empty', 'reasoning_only'])
@pytest.mark.parametrize('limited', [False, True])
@pytest.mark.parametrize('reasoning_format', [None, 'thinking'])
async def test_terminal_thoughts_do_not_move_before_completed_tools(
    monkeypatch, protocol, mode, limited, reasoning_format, tool_images=False
):
    saved, events, ctx = context(monkeypatch, protocol)
    payloads, executed = ([], [])

    async def lookup(q):
        executed.append(q)
        return 'result:' + q

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }

    def wire(req):
        payloads.append(json.loads(req.content))
        return (
            provider_response(protocol, [], req)
            if len(payloads) == 1
            else terminal_response(protocol, req, mode, limited)
        )

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding(protocol),
            connection(protocol),
            'terminal-' + str(len(payloads)),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    expected = 2 if protocol == 'ollama' else 1
    assert len(payloads) == 2 and len(executed) == expected, (payloads, executed, events)
    immediate_calls, immediate_results = pairs(protocol, payloads[-1])
    assert len(immediate_calls) == len(immediate_results) == expected
    saved.update(role='assistant', model='oi:leaf')
    if tool_images:
        result = next(item for item in saved['output'] if item.get('type') == 'function_call_output')
        result['output'].append(
            {
                'type': 'input_image',
                'image_url': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7X8AAAAASUVORK5CYII=',
            }
        )
    before = copy.deepcopy(saved)
    results = []
    for portable in [False, True]:
        from open_webui.utils.oi.native_privacy import replay_envelope

        candidate = copy.deepcopy(saved)
        if portable:
            candidate['oi_native'] = [replay_envelope(n) for n in candidate['oi_native']]
        error, payload = (None, None)
        try:
            normalized = middleware.sanitize_tool_pairs(
                middleware.process_messages_with_output([candidate], reasoning_format=reasoning_format)
            )
            if tool_images:
                media_index = next(
                    i for i, m in enumerate(normalized) if m.get('role') == 'user' and 'data:image' in json.dumps(m)
                )
                terminal_index = next(
                    i for i, m in enumerate(normalized) if (m.get('oi_native') or {}).get('call_id') == 'terminal-1'
                )
                assert terminal_index > media_index, normalized
            _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
            calls, outcomes = pairs(protocol, payload)
            if not len(calls) == len(outcomes) == expected:
                error = {'calls': calls, 'results': outcomes, 'expected': expected}
            elif mode == 'reasoning_only' and protocol != 'cpa-agy':
                marker = (
                    'terminal-thought'
                    if protocol == 'ollama'
                    else 'history-thought-3'
                    if protocol == 'gemini'
                    else 'signed-3'
                )
                encoded = json.dumps(payload)
                if marker not in encoded or encoded.index(marker) < encoded.index('result:'):
                    error = {'misplaced_or_missing_terminal_reasoning': marker}
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        results.append({'portable': portable, 'error': error, 'payload': payload})
    assert saved == before
    assert all((r['error'] is None for r in results)), results
