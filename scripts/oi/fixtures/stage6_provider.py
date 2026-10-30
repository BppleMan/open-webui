"""Loopback synthetic native protocols with captured outbound payloads."""

import json
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
# This loopback-only synthetic fixture also exercises browser-native transport.
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
events = []


@app.get('/events')
async def observed():
    return events


@app.get('/{protocol}/{path:path}')
async def models(protocol: str, path: str):
    return {'data': [{'id': 'fixture'}], 'models': [{'name': 'fixture'}]}


@app.post('/{protocol}/{path:path}')
async def generate(protocol: str, path: str, request: Request):
    b = await request.json()
    events.append(
        {
            'protocol': protocol,
            'path': path,
            'body': b,
            'rp': {k: request.headers.get(k) for k in ['x-rp-client', 'x-rp-user-id', 'x-rp-chat-id']},
        }
    )
    if b.get('fixture_error'):
        return JSONResponse({'error': {'message': 'Synthetic limit'}}, 429, headers={'Retry-After': '5'})
    text = 'Native stage6 fixture.'
    stream = b.get('stream', False) or 'streamGenerateContent' in path
    if protocol == 'openai-chat':
        body = {
            'id': 'r',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 5, 'prompt_tokens_details': {'cached_tokens': 20}},
        }
        frames = [
            {'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': body['usage']},
        ]
    elif protocol == 'openai-responses':
        item = {
            'id': 'm',
            'type': 'message',
            'role': 'assistant',
            'status': 'completed',
            'content': [{'type': 'output_text', 'text': text, 'annotations': []}],
        }
        body = {
            'id': 'r',
            'object': 'response',
            'instructions': b.get('instructions'),
            'created_at': 1,
            'status': 'completed',
            'model': 'fixture',
            'output': [item],
            'usage': {'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105},
        }
        frames = [
            {'type': 'response.created', 'response': {**body, 'status': 'in_progress', 'output': []}},
            {'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'content': []}},
            {
                'type': 'response.output_text.delta',
                'item_id': 'm',
                'output_index': 0,
                'content_index': 0,
                'delta': text,
            },
            {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
            {'type': 'response.completed', 'response': body},
        ]
    elif protocol == 'anthropic':
        body = {
            'id': 'r',
            'type': 'message',
            'role': 'assistant',
            'model': 'fixture',
            'content': [{'type': 'text', 'text': text}],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 80, 'cache_read_input_tokens': 20, 'output_tokens': 5},
        }
        frames = [
            {
                'type': 'message_start',
                'message': {
                    **body,
                    'content': [],
                    'usage': {'input_tokens': 80, 'cache_read_input_tokens': 20, 'output_tokens': 0},
                },
            },
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 5}},
            {'type': 'message_stop'},
        ]
    elif protocol in {'gemini', 'cpa-agy'}:
        body = {
            'candidates': [
                {
                    'content': {'role': 'model', 'parts': [{'text': text, 'thoughtSignature': 'opaque'}]},
                    'finishReason': 'STOP',
                    'groundingMetadata': {'fixture': True},
                    **({'cpaFinishReasonSource': 'provider'} if protocol == 'cpa-agy' else {}),
                }
            ],
            'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 5, 'cachedContentTokenCount': 20},
            'modelVersion': 'fixture',
        }
        frames = [body]
        if b.get('generationConfig', {}).get('thinkingConfig', {}).get('includeThoughts') is True:
            part = {'thought': True, 'text': 'Synthetic streamed thought summary.'}
            body['candidates'][0]['content']['parts'].insert(0, part)
            # Separate the reasoning delta from the final answer, as a real provider does.
            frames = [
                {'candidates': [{'content': {'role': 'model', 'parts': [part]}}]},
                {
                    **body,
                    'candidates': [
                        {
                            **body['candidates'][0],
                            'content': {'role': 'model', 'parts': body['candidates'][0]['content']['parts'][1:]},
                        }
                    ],
                },
            ]
        case = b.get('fixture_case')
        if case == 'blocked':
            body = {'promptFeedback': {'blockReason': 'SAFETY'}, 'usageMetadata': {'promptTokenCount': 3}}
            frames = [body]
        elif case in {'reasoning_only', 'image_only', 'mixed_image'}:
            png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y9bUAAAAASUVORK5CYII='
            parts = (
                [{'thought': True, 'text': 'Synthetic brief thought.', 'thoughtSignature': 'opaque'}]
                if case == 'reasoning_only'
                else [{'inlineData': {'mimeType': 'image/png', 'data': png}, 'thoughtSignature': 'opaque-image'}]
            )
            if case == 'mixed_image':
                parts.insert(0, {'text': 'Synthetic generated image.'})
            body['candidates'][0]['content']['parts'] = parts
            body['candidates'][0]['finishReason'] = 'MAX_TOKENS' if case == 'reasoning_only' else 'STOP'
            body['usageMetadata'].update(
                candidatesTokenCount=0 if case == 'reasoning_only' else 5, thoughtsTokenCount=4
            )
            frames = [body]
    elif protocol == 'gemini-interactions':
        step = {'type': 'model_output', 'content': [{'type': 'text', 'text': text}]}
        body = {
            'id': 'r',
            'status': 'completed',
            'steps': [step],
            'usage': {'total_input_tokens': 100, 'total_output_tokens': 5},
        }
        frames = [
            {'event_type': 'interaction.created', 'interaction': {'id': 'r', 'status': 'in_progress'}},
            {'event_type': 'step.start', 'index': 0, 'step': {'type': 'model_output', 'content': []}},
            {'event_type': 'step.delta', 'index': 0, 'delta': {'type': 'text', 'text': text}},
            {'event_type': 'step.stop', 'index': 0},
            {'event_type': 'interaction.completed', 'interaction': {'status': 'completed', 'usage': body['usage']}},
        ]
    elif protocol == 'ollama':
        body = {
            'model': 'fixture',
            'message': {'role': 'assistant', 'content': text, 'thinking': 'synthetic thought'},
            'done': True,
            'done_reason': 'stop',
            'prompt_eval_count': 100,
            'eval_count': 5,
        }
        frames = [body]
    else:
        return JSONResponse({'error': 'unsupported fixture'}, 404)
    if not stream:
        return body

    async def chunks():
        for frame in frames:
            data = json.dumps(frame, ensure_ascii=False)
            yield (data + '\n') if protocol == 'ollama' else ('data: ' + data + '\n\n')

    return StreamingResponse(
        chunks(), media_type='application/x-ndjson' if protocol == 'ollama' else 'text/event-stream'
    )
