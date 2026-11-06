import copy, json
import pytest
from open_webui.utils.middleware import process_messages_with_output
from open_webui.utils.oi.protocols import build_request
from test.oi.test_review_regressions import binding, connection


def text_output(value):
    return [
        {
            'type': 'message',
            'role': 'assistant',
            'status': 'completed',
            'content': [{'type': 'output_text', 'text': value}],
        }
    ]


def native(cid, content, extra, consumer):
    return {
        'call_id': cid,
        'binding_id': 'oi:leaf',
        'protocol': 'openai-chat',
        'replayable': True,
        'body': {'choices': [{'message': {'role': 'assistant', 'content': content, **extra}, 'finish_reason': 'stop'}]},
        'consumer_output': copy.deepcopy(consumer),
    }


@pytest.mark.parametrize('kind', ['text', 'audio', 'refusal'])
@pytest.mark.parametrize('continued', [False, True])
def test_nontext_terminal_is_preserved_in_followup(kind, continued):
    marker = 'synthetic-terminal-' + kind
    extra = {
        'audio': {'id': marker, 'transcript': 'synthetic spoken answer'},
        'refusal': marker,
        'images': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + marker}}],
    }
    tail_content = marker if kind == 'text' else ''
    tail_extra = {} if kind == 'text' else {kind: extra[kind]}
    tail_output = text_output(tail_content)
    first_output = text_output('previous answer')
    saved = {
        'id': 'reply',
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': ('previous answer' if continued else '') + tail_content,
        'output': (first_output if continued else []) + tail_output,
        'oi_native': ([native('first', 'previous answer', {}, first_output)] if continued else [])
        + [native('last', tail_content, tail_extra, tail_output)],
    }
    normalized = process_messages_with_output([saved])
    _, outbound = build_request({'messages': normalized}, binding('openai-chat'), connection('openai-chat'))
    assert marker in json.dumps(outbound), {
        'kind': kind,
        'continued': continued,
        'normalized': normalized,
        'outbound': outbound,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['audio', 'refusal'])
@pytest.mark.parametrize('stream', [False, True])
async def test_live_continue_preserves_nontext_terminal(monkeypatch, kind, stream):
    import httpx
    from open_webui.utils import middleware
    from open_webui.utils.oi import protocols
    from test.oi.test_round9_protocols import context
    from test.oi.round10_helpers import text_response

    saved, events, ctx = context(monkeypatch, 'openai-chat')
    marker = 'actual-pipeline-' + kind

    async def generate(cid, wire):
        return await protocols.generate(
            ctx['request'],
            ctx['form_data'],
            ctx['user'],
            binding('openai-chat'),
            connection('openai-chat'),
            cid,
            httpx.MockTransport(wire),
        )

    await middleware.process_chat_response(
        await generate('first', lambda _: text_response('openai-chat', ['previous answer'])), ctx
    )
    saved.update(id='reply', role='assistant', model='oi:leaf')
    ctx['assistant_message'] = copy.deepcopy(saved)
    ctx['metadata']['assistant_message_id'] = 'reply'
    delta = {kind: marker if kind == 'refusal' else {'id': marker, 'transcript': 'spoken continuation'}}
    ctx['form_data']['stream'] = stream
    body = {'choices': [{'index': 0, 'delta' if stream else 'message': delta, 'finish_reason': 'stop'}]}
    response = lambda _: (
        httpx.Response(200, content='data: ' + json.dumps(body) + '\n\n') if stream else httpx.Response(200, json=body)
    )
    await middleware.process_chat_response(await generate('last', response), ctx)
    assert marker in json.dumps(saved['oi_native']), saved
    normalized = process_messages_with_output([saved])
    _, outbound = build_request({'messages': normalized}, binding('openai-chat'), connection('openai-chat'))
    assert marker in json.dumps(outbound), {
        'kind': kind,
        'saved': saved,
        'normalized': normalized,
        'outbound': outbound,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['audio', 'refusal'])
async def test_live_tool_terminal_preserves_nontext(monkeypatch, kind):
    import httpx
    from open_webui.utils import middleware
    from open_webui.utils.oi import protocols
    from test.oi.test_round9_protocols import context
    from test.oi.test_native_tool_roundtrip import provider_response

    saved, events, ctx = context(monkeypatch, 'openai-chat')
    marker = 'actual-tool-terminal-' + kind
    executed = []

    async def lookup(q):
        executed.append(q)
        return 'completed tool result'

    ctx['metadata']['tools'] = {
        'lookup': {
            'callable': lookup,
            'spec': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}},
        }
    }
    count = 0

    def wire(req):
        nonlocal count
        count += 1
        if count == 1:
            return provider_response('openai-chat', [], req)
        delta = {kind: marker if kind == 'refusal' else {'id': marker, 'transcript': 'spoken final answer'}}
        body = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': 'stop'}]}
        return httpx.Response(200, content='data: ' + json.dumps(body) + '\n\n')

    async def generate(request, form, user, **kwargs):
        return await protocols.generate(
            request,
            form,
            user,
            binding('openai-chat'),
            connection('openai-chat'),
            'tool-call-' + str(count),
            httpx.MockTransport(wire),
        )

    monkeypatch.setattr(middleware, 'generate_chat_completion', generate)
    await middleware.process_chat_response(await generate(ctx['request'], ctx['form_data'], ctx['user']), ctx)
    saved.update(id='reply', role='assistant', model='oi:leaf')
    assert executed and marker in json.dumps(saved['oi_native']), saved
    normalized = process_messages_with_output([saved])
    _, outbound = build_request({'messages': normalized}, binding('openai-chat'), connection('openai-chat'))
    assert marker in json.dumps(outbound), {'kind': kind, 'saved': saved, 'outbound': outbound}
