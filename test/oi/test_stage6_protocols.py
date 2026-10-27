import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from open_webui.utils.oi.protocols import NativeStream, build_request, save_native
from open_webui.utils.oi.catalog import reasoning_controls, discovered_capabilities
from open_webui.utils.payload import apply_model_controls
from open_webui.utils import context_compaction as cc


def binding(protocol):
    return SimpleNamespace(
        id='oi:fixture', protocol=protocol, upstream_id='fixture', capabilities={'output_limit': 1024}, params={}
    )


def conn():
    return SimpleNamespace(base_url='https://fixture.invalid/v1', template='fixture', settings={})


def test_gemini_signature_envelope_is_not_mutated_to_invent_tool_ids():
    body = {
        'candidates': [
            {
                'content': {
                    'role': 'model',
                    'parts': [{'functionCall': {'name': 'lookup', 'args': {'x': 1}}, 'thoughtSignature': 'opaque'}],
                },
                'finishReason': 'STOP',
            }
        ]
    }
    original = copy.deepcopy(body)
    state = NativeStream('gemini')
    result = state.nonstream(body)
    assert state.body == original
    tool = result['choices'][0]['message']['tool_calls'][0]
    messages = [
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': [tool],
            'oi_native': {'binding_id': 'oi:fixture', 'protocol': 'gemini', 'body': state.body},
        },
        {'role': 'tool', 'tool_call_id': tool['id'], 'content': '{"ok":true}'},
    ]
    _, out = build_request({'messages': messages}, binding('gemini'), conn())
    assert out['contents'][0] == original['candidates'][0]['content']
    assert out['contents'][1]['parts'][0]['functionResponse'] == {'name': 'lookup', 'response': {'ok': True}}


@pytest.mark.parametrize('protocol', ['anthropic', 'openai-responses', 'gemini-interactions'])
def test_signed_blocks_survive_interruption_but_incomplete_envelope_is_not_replayed(protocol):
    state = NativeStream(protocol)
    if protocol == 'anthropic':
        state.feed({'type': 'message_start', 'message': {'content': [], 'usage': {'input_tokens': 12}}})
        state.feed(
            {
                'type': 'content_block_start',
                'index': 0,
                'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''},
            }
        )
        state.feed(
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'opaque'}}
        )
        assert state.body['content'][0]['signature'] == 'opaque'
    elif protocol == 'openai-responses':
        state.feed(
            {
                'type': 'response.output_item.done',
                'output_index': 0,
                'item': {'type': 'reasoning', 'encrypted_content': 'opaque', 'summary': []},
            }
        )
        assert state.body['output'][0]['encrypted_content'] == 'opaque'
    else:
        state.feed({'event_type': 'step.start', 'index': 0, 'step': {'type': 'thought'}})
        state.feed(
            {'event_type': 'step.delta', 'index': 0, 'delta': {'type': 'thought_signature', 'signature': 'opaque'}}
        )
        assert state.body['steps'][0]['signature'] == 'opaque'
    msg = {
        'role': 'assistant',
        'content': 'visible text',
        'oi_native': {'protocol': protocol, 'binding_id': 'oi:fixture', 'body': state.body, 'replayable': False},
    }
    _, payload = build_request({'messages': [msg], 'max_tokens': 100}, binding(protocol), conn())
    assert 'opaque' not in str(payload) and 'visible text' in str(payload)


def test_cpa_usage_and_synthetic_completion_provenance():
    state = NativeStream('cpa-agy')
    events = state.feed(
        {'cpaUsageMetadata': {'promptTokenCount': 123}, 'candidates': [{'content': {'parts': [{'text': 'partial'}]}}]}
    )
    assert events[-1]['usage']['input_tokens'] == 123
    end = state.feed(
        {'candidates': [{'finishReason': 'STOP', 'cpaFinishReasonSource': 'synthetic_done', 'content': {'parts': []}}]}
    )[-1]
    assert end['oi_finish']['status'] == 'incomplete' and end['oi_finish']['source'] == 'synthetic_done'


def test_discovered_thinking_is_optional_and_never_sets_an_unknown_default():
    caps = discovered_capabilities({'thinkingSupport': {'levels': ['low', 'high'], 'dynamic_allowed': True}})
    b = binding('gemini')
    b.capabilities = caps
    controls = reasoning_controls(b)
    assert controls['oi_reasoning']['default'] is None
    assert apply_model_controls({}, controls, {}) == {}
    out = apply_model_controls({}, controls, {'oi_reasoning': 'level_1'})
    assert out['custom_params']['generationConfig']['thinkingConfig']['thinkingLevel'] == 'high'
    assert reasoning_controls(binding('gemini')) == {}


@pytest.mark.asyncio
async def test_manual_checkpoint_applies_without_automatic_summary_or_history_mutation(monkeypatch):
    monkeypatch.setattr(cc, '_load_config', AsyncMock(return_value={'enable': True}))
    summary = AsyncMock()
    monkeypatch.setattr(cc, '_generate_summary', summary)
    messages = [
        {'role': 'system', 'content': 'system'},
        {'role': 'user', 'content': 'original'},
        {'role': 'user', 'content': 'recent', 'contextSummary': 'explicit manual summary'},
    ]
    original = copy.deepcopy(messages)
    out, text, changed = await cc.compact_messages_for_request(None, None, messages, {}, 'm', {})
    assert [m['content'] for m in out] == ['system', 'recent'] and text == 'explicit manual summary' and not changed
    summary.assert_not_called()
    assert messages == original
    monkeypatch.setattr(cc, '_load_config', AsyncMock(return_value={'enable': False}))
    assert (await cc.compact_messages_for_request(None, None, messages, {}, 'm', {})) == (messages, None, False)


@pytest.mark.asyncio
async def test_background_summary_cannot_replace_reply_native_state(monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr('open_webui.utils.oi.protocols.Chats.upsert_message_to_chat_by_id_and_message_id', write)
    await save_native(
        {'task': 'context_compaction', 'chat_id': 'chat', 'message_id': 'message'},
        binding('gemini'),
        'call',
        {'content': 'summary'},
    )
    write.assert_not_called()


@pytest.mark.parametrize(
    'protocol', ['openai-chat', 'openai-responses', 'anthropic', 'gemini', 'gemini-interactions', 'cpa-agy', 'ollama']
)
def test_image_input_uses_native_shape_without_changing_input(protocol):
    form = {
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'synthetic'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,QUJD'}},
                ],
            }
        ],
        'max_tokens': 64,
    }
    original = copy.deepcopy(form)
    _, body = build_request(form, binding(protocol), conn())
    assert form == original
    if protocol == 'openai-chat':
        assert body['messages'][0]['content'][1]['type'] == 'image_url'
    elif protocol == 'openai-responses':
        assert body['input'][0]['content'][1]['type'] == 'input_image'
    elif protocol == 'anthropic':
        assert body['messages'][0]['content'][1]['source']['data'] == 'QUJD'
    elif protocol in {'gemini', 'cpa-agy'}:
        assert body['contents'][0]['parts'][1]['inlineData']['mimeType'] == 'image/png'
    elif protocol == 'gemini-interactions':
        assert body['input'][0]['content'][1] == {'type': 'image', 'data': 'QUJD', 'mime_type': 'image/png'}
    else:
        assert body['messages'][0]['images'] == ['QUJD']


def test_interactions_audio_document_tool_result_and_failure_usage():
    from open_webui.utils.oi.protocols import interaction_parts

    assert interaction_parts(
        [
            {'type': 'input_audio', 'input_audio': {'data': 'QUJD', 'format': 'wav'}},
            {'type': 'file', 'file': {'file_data': 'data:application/pdf;base64,QUJD'}},
        ]
    ) == [
        {'type': 'audio', 'data': 'QUJD', 'mime_type': 'audio/wav'},
        {'type': 'document', 'data': 'QUJD', 'mime_type': 'application/pdf'},
    ]
    state = NativeStream('gemini-interactions')
    events = state.feed(
        {
            'event_type': 'interaction.failed',
            'interaction': {'status': 'failed', 'error': {'message': 'failure'}, 'usage': {'total_input_tokens': 12}},
        }
    )
    assert events[0]['usage']['input_tokens'] == 12 and events[-1]['error']


@pytest.mark.asyncio
async def test_empty_compaction_does_not_fabricate_a_truncated_summary(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(cc.Config, 'get_many', AsyncMock(return_value={}))
    monkeypatch.setattr(
        'open_webui.utils.chat.generate_chat_completion',
        AsyncMock(return_value={'choices': [{'message': {'content': ''}}]}),
    )
    with pytest.raises(HTTPException, match='original history was preserved'):
        await cc._generate_summary(
            SimpleNamespace(state=SimpleNamespace()),
            SimpleNamespace(id='u'),
            'm',
            {'m': {'id': 'm'}},
            [{'role': 'user', 'content': 'important original'}],
            [],
            None,
            '',
        )


def test_openai_stream_nullable_initial_content_and_tool_fields():
    state = NativeStream('openai-chat')
    state.feed({'choices': [{'delta': {'role': 'assistant', 'content': None, 'tool_calls': None}}]})
    state.feed({'choices': [{'delta': {'content': 'OK'}}]})
    state.feed({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10}})
    assert state.body['choices'][0]['message']['content'] == 'OK'
    assert state.finished and state.usage['prompt_tokens'] == 10
