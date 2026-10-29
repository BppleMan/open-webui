"""Summary visibility must not change reasoning effort, signatures or billing."""

import copy
import json

import pytest
from fastapi import HTTPException
from open_webui.utils.oi.catalog import thought_controls
from open_webui.utils.oi.protocols import NativeStream, build_request
from open_webui.utils.payload import apply_model_controls, apply_params_to_form_data
from test.oi.test_protocols import binding, connection


@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
@pytest.mark.parametrize('transport', ['server', 'browser'])
@pytest.mark.parametrize('choice', [None, 'on', 'off'])
def test_summary_control_preserves_model_params_and_transport(protocol, transport, choice):
    b, c = binding(protocol), connection(protocol)
    c.transport = transport
    params = {
        'provider_options': {
            'generationConfig': {
                'temperature': 0.4,
                'thinkingConfig': {'thinkingBudget': 2048, 'includeThoughts': False},
            }
        }
    }
    original = copy.deepcopy(params)
    controls = thought_controls(b)
    resolved = apply_model_controls(copy.deepcopy(params), controls, {'oi_thoughts': choice} if choice else {})
    form = apply_params_to_form_data({'params': resolved, 'stream': True, 'messages': []}, {})
    url, body = build_request(form, b, c)
    assert 'streamGenerateContent' in url and 'stream' not in body
    assert body['generationConfig'] == {
        'temperature': 0.4,
        'thinkingConfig': {'thinkingBudget': 2048, 'includeThoughts': choice == 'on'},
    }
    assert params == original


@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
def test_default_summary_does_not_invent_effort_or_mutate_input(protocol):
    form = {'messages': [], 'generationConfig': {'thinkingConfig': {'thinkingLevel': 'low'}}}
    before = copy.deepcopy(form)
    _, body = build_request(form, binding(protocol), connection(protocol))
    assert body['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'low', 'includeThoughts': True}
    assert form == before
    form['generationConfig']['thinkingConfig'] = {'thinkingBudget': 0, 'includeThoughts': False}
    _, body = build_request(form, binding(protocol), connection(protocol))
    assert body['generationConfig']['thinkingConfig'] == {'thinkingBudget': 0, 'includeThoughts': False}


@pytest.mark.parametrize('bad', ['false', None, 1])
def test_invalid_summary_setting_fails_locally(bad):
    with pytest.raises(HTTPException) as error:
        build_request(
            {'generationConfig': {'thinkingConfig': {'includeThoughts': bad}}}, binding('gemini'), connection('gemini')
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize('protocol', ['openai-chat', 'openai-responses', 'anthropic', 'ollama', 'gemini-interactions'])
def test_summary_control_does_not_leak_across_protocols(protocol):
    assert thought_controls(binding(protocol)) == {}


@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy'])
def test_summary_deltas_arrive_before_body_and_preserve_native_signature(protocol):
    stream = NativeStream(protocol)
    thought = {
        'candidates': [{'content': {'role': 'model', 'parts': [{'thought': True, 'text': 'Synthetic thought. '}]}}],
        'usageMetadata': {'promptTokenCount': 100, 'thoughtsTokenCount': 2},
    }
    first = stream.feed(thought)
    assert any(
        e.get('choices', [{}])[0].get('delta', {}).get('reasoning_content') == 'Synthetic thought. ' for e in first
    )
    assert not any(e.get('choices', [{}])[0].get('delta', {}).get('content') for e in first)
    # These deltas exist before any finish event, so a disconnected stream can retain them.
    second = stream.feed(
        {'candidates': [{'content': {'parts': [{'thought': True, 'text': 'Next.', 'thoughtSignature': 'opaque'}]}}]}
    )
    assert 'Next.' in json.dumps(second)
    end = {
        'candidates': [{'content': {'parts': [{'text': 'Answer.'}]}, 'finishReason': 'STOP'}],
        'usageMetadata': {'promptTokenCount': 100, 'thoughtsTokenCount': 2, 'candidatesTokenCount': 3},
    }
    stream.feed(end)
    stored = json.loads(json.dumps(stream.body))
    restored = NativeStream(protocol).nonstream(stored)
    message = restored['choices'][0]['message']
    assert message['reasoning_content'] == 'Synthetic thought. Next.'
    assert message['content'] == 'Answer.'
    assert stored['candidates'][0]['content']['parts'][0]['thoughtSignature'] == 'opaque'
    assert restored['usage']['output_tokens'] == 5
    native = {'binding_id': 'oi:binding', 'protocol': protocol, 'body': stored}
    _, following = build_request(
        {'messages': [{'role': 'assistant', 'content': 'Answer.', 'oi_native': native}]},
        binding(protocol),
        connection(protocol),
    )
    if protocol == 'cpa-agy':
        assert not any(p.get('thought') for p in following['contents'][0]['parts'])
    else:
        assert following['contents'][0]['parts'][0]['thoughtSignature'] == 'opaque'


def test_tokens_without_summary_do_not_invent_reasoning_text():
    message = NativeStream('gemini').nonstream(
        {
            'candidates': [
                {'content': {'parts': [{'text': 'Answer.', 'thoughtSignature': 'opaque'}]}, 'finishReason': 'STOP'}
            ],
            'usageMetadata': {'promptTokenCount': 100, 'thoughtsTokenCount': 2, 'candidatesTokenCount': 3},
        }
    )['choices'][0]['message']
    assert not message.get('reasoning_content')
