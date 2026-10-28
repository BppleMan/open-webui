import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from open_webui.utils.oi.catalog import encrypt_credentials
from open_webui.utils.oi.protocols import NativeStream, build_request, generate
from open_webui.utils.oi.runtime import SSEDecoder


def binding(protocol):
    return SimpleNamespace(
        id='oi:binding', upstream_id='native-model', protocol=protocol, capabilities={'output_limit': 4096}, params={}
    )


def connection(protocol):
    return SimpleNamespace(
        id='connection',
        protocol=protocol,
        template=protocol,
        base_url='https://provider.invalid/v1beta',
        credentials=encrypt_credentials({'api_key': 'synthetic'}),
        settings={},
    )


def test_responses_preserves_system_order_tools_and_multimodal():
    messages = [
        {'role': 'system', 'content': 'A'},
        {'role': 'developer', 'content': 'B'},
        {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': 'look'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}},
            ],
        },
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': [{'id': 't1', 'type': 'function', 'function': {'name': 'dice', 'arguments': '{"sides":6}'}}],
        },
        {'role': 'tool', 'tool_call_id': 't1', 'content': '4'},
    ]
    original = copy.deepcopy(messages)
    url, body = build_request(
        {'messages': messages, 'metadata': {'secret': 'never-send'}, 'max_tokens': 1024},
        binding('openai-responses'),
        connection('openai-responses'),
    )
    assert url.endswith('/responses')
    assert [m.get('role') for m in body['input'][:3]] == ['system', 'developer', 'user']
    assert body['input'][2]['content'][1]['image_url'].startswith('data:')
    assert body['input'][-2]['call_id'] == body['input'][-1]['call_id'] == 't1'
    assert body['store'] is False and body['truncation'] == 'disabled'
    assert 'metadata' not in body and body['model'] == 'native-model'
    assert messages == original


@pytest.mark.parametrize('protocol', ['anthropic', 'gemini', 'cpa-agy', 'gemini-interactions'])
def test_native_refuses_reordering_mid_conversation_instructions(protocol):
    with pytest.raises(HTTPException) as error:
        build_request(
            {'messages': [{'role': 'user', 'content': 'a'}, {'role': 'system', 'content': 'b'}]},
            binding(protocol),
            connection(protocol),
        )
    assert error.value.status_code == 422


def test_anthropic_usage_signature_and_tool_replay():
    state = NativeStream('anthropic')
    events = [
        {
            'type': 'message_start',
            'message': {
                'id': 'msg',
                'content': [],
                'usage': {'input_tokens': 10, 'cache_read_input_tokens': 90, 'output_tokens': 1},
            },
        },
        {
            'type': 'content_block_start',
            'index': 0,
            'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''},
        },
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'reason'}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'opaque-signed'}},
        {
            'type': 'content_block_start',
            'index': 1,
            'content_block': {'type': 'tool_use', 'id': 'tool-1', 'name': 'dice', 'input': {}},
        },
        {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '{"sides":'}},
        {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '6}'}},
        {'type': 'content_block_stop', 'index': 1},
        {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 20}},
        {'type': 'message_stop'},
    ]
    result = [chunk for event in events for chunk in state.feed(event)]
    assert result[-1]['usage']['input_tokens'] == 100
    assert result[-1]['usage']['output_tokens'] == 20
    assert result[-1]['choices'][0]['delta']['tool_calls'][0]['function']['arguments'] == '{"sides":6}'
    native = {'protocol': 'anthropic', 'binding_id': 'oi:binding', 'body': state.body}
    _, payload = build_request(
        {
            'messages': [
                {'role': 'assistant', 'oi_native': native},
                {'role': 'tool', 'tool_call_id': 'tool-1', 'content': '4'},
            ]
        },
        binding('anthropic'),
        connection('anthropic'),
    )
    assert payload['messages'][0]['content'][0]['signature'] == 'opaque-signed'
    assert payload['messages'][1]['content'][0]['tool_use_id'] == 'tool-1'


def test_gemini_native_fields_signatures_and_cpa_contract():
    state = NativeStream('cpa-agy')
    native = {
        'candidates': [
            {
                'content': {
                    'role': 'model',
                    'parts': [
                        {'text': 'thought', 'thought': True, 'thoughtSignature': 'private-thinking'},
                        {
                            'functionCall': {'id': 't', 'name': 'dice', 'args': {'sides': 6}},
                            'thoughtSignature': 'tool-signature',
                        },
                    ],
                },
                'groundingMetadata': {'groundingChunks': [{'web': {'uri': 'https://example.invalid'}}]},
                'finishReason': 'STOP',
            }
        ],
        'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 5, 'thoughtsTokenCount': 10},
        'extension': {'finish_source': 'upstream'},
    }
    result = state.feed(native)
    assert result[-1]['usage']['output_tokens'] == 15
    assert state.body['extension'] == native['extension']
    assert state.body['candidates'][0]['groundingMetadata'] == native['candidates'][0]['groundingMetadata']
    _, body = build_request(
        {
            'messages': [
                {
                    'role': 'assistant',
                    'oi_native': {'protocol': 'cpa-agy', 'binding_id': 'oi:binding', 'body': state.body},
                },
                {'role': 'tool', 'tool_call_id': 't', 'content': '4'},
            ],
            'generationConfig': {'thinkingConfig': {'thinkingLevel': 'high'}},
            'provider_options': {'labels': {'run': 'synthetic'}},
        },
        binding('cpa-agy'),
        connection('cpa-agy'),
    )
    assert body['contents'][0]['parts'] == [native['candidates'][0]['content']['parts'][1]]
    assert body['contents'][1]['parts'][0]['functionResponse']['name'] == 'dice'
    assert body['generationConfig']['thinkingConfig']['thinkingLevel'] == 'high'
    assert body['labels']['run'] == 'synthetic'


def test_utf8_sse_chunk_boundaries_and_duplicate_snapshots():
    parser = SSEDecoder()
    source = (
        'data: '
        + json.dumps({'text': '中文'}, ensure_ascii=False)
        + '\r\n\r\ndata: {"usage":{"input_tokens":12}}\n\ndata: [DONE]\n\n'
    ).encode()
    events = []
    for byte in source:
        events += parser.feed(bytes([byte]))
    assert events == [{'text': '中文'}, {'usage': {'input_tokens': 12}}]


@pytest.mark.asyncio
async def test_cpa_429_is_not_retried_and_identity_is_trusted():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers['X-RP-User-ID'] == 'u'
        assert request.headers['X-RP-Chat-ID'] == 'temporary-stable-id'
        return httpx.Response(429, json={'error': {'code': 'quota_exhausted'}}, headers={'Retry-After': '60'})

    request = SimpleNamespace(state=SimpleNamespace(metadata={}))
    result = await generate(
        request,
        {
            'messages': [{'role': 'user', 'content': 'hello'}],
            'metadata': {'chat_id': 'temporary:socket', 'oi_temporary_id': 'temporary-stable-id'},
        },
        SimpleNamespace(id='u'),
        binding('cpa-agy'),
        connection('cpa-agy'),
        'call',
        transport=httpx.MockTransport(handler),
    )
    assert result.status_code == 429 and len(requests) == 1
    assert result.headers['retry-after'] == '60'


def test_parallel_requests_do_not_share_generation_state():
    from open_webui.utils.oi.runtime import fork_request
    from starlette.requests import Request

    parent = Request(
        {'type': 'http', 'method': 'POST', 'path': '/', 'headers': [], 'state': {'metadata': {'chat_id': 'chat'}}}
    )
    left = fork_request(parent, {'chat_id': 'chat', 'message_id': 'left'})
    right = fork_request(parent, {'chat_id': 'chat', 'message_id': 'right'})
    left.state.metadata['oi_generation_id'] = 'left-generation'
    right.state.metadata['oi_generation_id'] = 'right-generation'
    assert left.state.metadata['message_id'] == 'left'
    assert right.state.metadata['message_id'] == 'right'
    assert 'oi_generation_id' not in parent.state.metadata


@pytest.mark.parametrize(
    'protocol', ['openai-chat', 'openai-responses', 'anthropic', 'gemini', 'gemini-interactions', 'cpa-agy', 'ollama']
)
def test_display_identity_does_not_change_outbound_context(protocol):
    body = {
        'model': 'oi:binding',
        'messages': [
            {'role': 'user', 'content': 'Synthetic question'},
            {'role': 'assistant', 'content': 'Unchanged answer'},
        ],
    }
    decorated = copy.deepcopy(body)
    decorated['messages'][1]['oi_model_identity'] = {
        'id': 'oi:binding',
        'name': 'Display only',
        'connection': {'id': 'connection', 'name': 'Origin'},
    }
    assert build_request(body, binding(protocol), connection(protocol)) == build_request(
        decorated, binding(protocol), connection(protocol)
    )


@pytest.mark.parametrize('suffix', ['', '/v1', '/v1/', '/v1beta', '/v1beta/'])
@pytest.mark.parametrize('prefix', ['', '/gateway/cpa'])
@pytest.mark.asyncio
async def test_cpa_service_addresses_share_native_discovery_and_generation(suffix, prefix):
    from open_webui.utils.oi.catalog import fetch_models, request_headers

    conn = connection('cpa-agy')
    conn.base_url = 'https://provider.invalid' + prefix + suffix
    base = 'https://provider.invalid' + prefix + '/v1beta'
    discovered = []

    def respond(request):
        discovered.append(str(request.url))
        return httpx.Response(200, json={'models': [{'name': 'models/native-model'}]})

    models = await fetch_models(conn, transport=httpx.MockTransport(respond))
    assert models == [{'name': 'models/native-model'}]
    assert discovered == [base + '/models']
    for stream in (False, True):
        url, payload = build_request(
            {'stream': stream, 'messages': [{'role': 'user', 'content': 'synthetic'}]},
            binding('cpa-agy'),
            conn,
        )
        assert url == base + '/models/native-model:' + (
            'streamGenerateContent?alt=sse' if stream else 'generateContent'
        )
        assert payload['contents'][0]['parts'] == [{'text': 'synthetic'}]
    headers = request_headers(conn, user_id='owner', chat_id='stable-chat')
    assert headers['X-RP-Chat-ID'] == 'stable-chat'
    assert headers['X-RP-User-ID'] == 'owner'


def test_cpa_native_root_does_not_rewrite_explicit_openai_protocol():
    conn = connection('cpa-agy')
    conn.base_url = 'https://provider.invalid/gateway/v1'
    url, _ = build_request({'messages': [{'role': 'user', 'content': 'synthetic'}]}, binding('openai-chat'), conn)
    assert url == 'https://provider.invalid/gateway/v1/chat/completions'
