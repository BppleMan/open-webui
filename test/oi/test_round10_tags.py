"""Actual adapter -> consumer -> Continue -> saved and portable history rebuild."""

import copy
import json

import httpx
import pytest
from open_webui.utils import middleware
from open_webui.utils.oi import protocols
from open_webui.utils.oi.native_privacy import replay_envelope
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection
from test.oi.round10_helpers import PROTOCOLS, text_response

CASES = {
    'mixed_reasoning': ['<reason>a</reason>x<think>b</think>y<re', 'ason>c</reason>z'],
    'solution_boundary': ['<think>a</think>', '<|begin_of_solution|>x<|end_of_solu', 'tion|>y<think>b</think>z'],
}


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('style', CASES)
@pytest.mark.parametrize('partitioned', [False, True])
@pytest.mark.parametrize('continued', [False, True])
async def test_unchanged_tag_history_survives_stream_partitioning(monkeypatch, protocol, style, partitioned, continued):
    pieces = CASES[style] if partitioned else [''.join(CASES[style])]
    saved, events, ctx = context(monkeypatch, protocol)
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'tag-first',
        httpx.MockTransport(lambda _: text_response(protocol, pieces)),
    )
    await middleware.process_chat_response(response, ctx)
    saved.update(id='reply', role='assistant', model='oi:leaf')
    assert saved.get('done') and saved.get('oi_native') and saved.get('output'), (saved, events)
    if continued:
        saved, events, ctx = context(monkeypatch, protocol, saved, assistant_message_id='reply')
        response = await protocols.generate(
            ctx['request'],
            ctx['form_data'],
            ctx['user'],
            binding(protocol),
            connection(protocol),
            'tag-continued',
            httpx.MockTransport(lambda _: text_response(protocol, ['more'])),
        )
        await middleware.process_chat_response(response, ctx)
    results = []
    original = copy.deepcopy(saved)
    for portable in [False, True]:
        candidate = copy.deepcopy(saved)
        if portable:
            candidate['oi_native'] = [replay_envelope(n) for n in candidate['oi_native']]
        error, payload = None, None
        try:
            normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([candidate]))
            _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
        except Exception as exc:
            error = str(exc)
        results.append({'portable': portable, 'error': error, 'payload': payload})
    assert saved == original
    assert all(r['error'] is None for r in results), results
    assert all(''.join(pieces) in json.dumps(r['payload']) for r in results)
    if continued:
        assert all('more' in json.dumps(r['payload']) for r in results)
