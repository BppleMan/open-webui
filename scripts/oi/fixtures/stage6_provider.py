"""Loopback synthetic native protocols with captured outbound payloads."""

import json
import asyncio
import copy
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
            'body': copy.deepcopy(b),
            'rp': {k: request.headers.get(k) for k in ['x-rp-client', 'x-rp-user-id', 'x-rp-chat-id']},
        }
    )
    last_content = (b.get('contents') or b.get('messages') or [{}])[-1]
    fixture_prompt = json.dumps(last_content)
    if 'R72_TAGS_STREAM' in fixture_prompt:
        b['fixture_case'] = 'round10_tags'
    elif 'R72_FILTER_STREAM' in fixture_prompt:
        b['fixture_case'] = 'round10_filter'
    slow_continue = (
        protocol in {'gemini', 'cpa-agy'}
        and last_content.get('role') == 'model'
        and 'R73 initial reply.' in fixture_prompt
    )
    if 'R73_TEMPORARY_NATIVE' in fixture_prompt:
        b['fixture_case'] = 'r73_initial'
    elif 'R73_ASK_USER' in fixture_prompt:
        b['fixture_case'] = 'r73_ask'
    slow_tools = b.get('fixture_case') == 'r73_partial_tool'
    if b.get('fixture_error'):
        return JSONResponse({'error': {'message': 'Synthetic limit'}}, 429, headers={'Retry-After': '5'})
    filter_fixture = b.get('fixture_case') == 'round10_filter' or (
        protocol == 'ollama' and 'Synthetic round10_filter' in str((b.get('messages') or [{}])[-1].get('content', ''))
    )
    text = (
        'R73 initial reply.'
        if b.get('fixture_case') == 'r73_initial'
        else 'round10-original'
        if filter_fixture
        else 'Native stage6 fixture.'
    )
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
        if b.get('fixture_case') == 'round10_tags':
            pieces = ['<reason>a</reason>x<think>b</think>y<re', 'ason>c</reason>z']
            frames = [
                {
                    'candidates': [
                        {
                            'content': {
                                'role': 'model',
                                'parts': [
                                    {
                                        'text': piece,
                                        **({'thoughtSignature': 'round10-signature'} if i == len(pieces) - 1 else {}),
                                    }
                                ],
                            },
                            **({'finishReason': 'STOP'} if i == len(pieces) - 1 else {}),
                        }
                    ]
                }
                for i, piece in enumerate(pieces)
            ]
        elif b.get('fixture_case') == 'round10_markdown':
            import base64, io
            from PIL import Image, PngImagePlugin

            image = io.BytesIO()
            info = PngImagePlugin.PngInfo()
            info.add_text('fixture', 'synthetic image metadata ' * 60)
            Image.new('RGB', (2, 2), 'white').save(image, format='PNG', pnginfo=info)
            markdown = 'A picture: ![image](data:image/png;base64,' + base64.b64encode(image.getvalue()).decode() + ')'
            body['candidates'][0]['content']['parts'] = [{'text': markdown}]
            frames = [body]
        case = b.get('fixture_case')
        if case == 'blocked':
            body = {'promptFeedback': {'blockReason': 'SAFETY'}, 'usageMetadata': {'promptTokenCount': 3}}
            frames = [body]
        elif case in {'reasoning_only', 'image_only', 'mixed_image', 'image_unsigned'}:
            png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y9bUAAAAASUVORK5CYII='
            parts = (
                [{'thought': True, 'text': 'Synthetic brief thought.', 'thoughtSignature': 'opaque'}]
                if case == 'reasoning_only'
                else [{'inlineData': {'mimeType': 'image/png', 'data': png}, 'thoughtSignature': 'opaque-image'}]
            )
            if case == 'mixed_image':
                parts.insert(0, {'text': 'Synthetic generated image.'})
            if case == 'image_unsigned':
                parts[0].pop('thoughtSignature')
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
    if b.get('fixture_case') == 'r73_ask' and protocol in {'gemini', 'cpa-agy'}:
        arguments = {
            'questions': [
                {
                    'id': 'route',
                    'header': 'Route',
                    'question': 'Choose a synthetic route.',
                    'options': [
                        {'label': 'North', 'description': 'Go north.'},
                        {'label': 'South', 'description': 'Go south.'},
                    ],
                }
            ]
        }
        body = {
            'candidates': [
                {
                    'content': {
                        'role': 'model',
                        'parts': [
                            {
                                'functionCall': {'id': 'r73-ask', 'name': 'ask_user', 'args': arguments},
                                'thoughtSignature': 'r73-ask-signature',
                            }
                        ],
                    },
                    'finishReason': 'STOP',
                }
            ]
        }
        frames = [body]
    if slow_continue:
        frames = [
            {'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'R73 partial continuation.'}]}}]},
            {
                'candidates': [
                    {'content': {'role': 'model', 'parts': [{'text': ' completed.'}]}, 'finishReason': 'STOP'}
                ]
            },
        ]
    if slow_tools and protocol == 'openai-chat':
        frames = [
            {
                'choices': [
                    {'index': 0, 'delta': {'role': 'assistant', 'content': 'saved-prefix'}, 'finish_reason': None}
                ]
            },
            {
                'choices': [
                    {
                        'index': 0,
                        'delta': {
                            'tool_calls': [
                                {
                                    'index': 0,
                                    'id': 'r73-partial',
                                    'type': 'function',
                                    'function': {'name': 'lookup', 'arguments': '{"q":"partial'},
                                }
                            ]
                        },
                        'finish_reason': None,
                    }
                ]
            },
        ]
    elif slow_tools and protocol == 'openai-responses':
        frames = [
            {
                'type': 'response.output_item.added',
                'output_index': 0,
                'item': {'type': 'message', 'id': 'm', 'role': 'assistant', 'content': []},
            },
            {
                'type': 'response.content_part.added',
                'item_id': 'm',
                'output_index': 0,
                'content_index': 0,
                'part': {'type': 'output_text', 'text': '', 'annotations': []},
            },
            {
                'type': 'response.output_text.delta',
                'item_id': 'm',
                'output_index': 0,
                'content_index': 0,
                'delta': 'saved-prefix',
            },
            {
                'type': 'response.output_item.added',
                'output_index': 1,
                'item': {
                    'type': 'function_call',
                    'id': 'fc',
                    'call_id': 'r73-partial',
                    'name': 'lookup',
                    'arguments': '',
                    'status': 'in_progress',
                },
            },
            {
                'type': 'response.function_call_arguments.delta',
                'item_id': 'fc',
                'output_index': 1,
                'delta': '{"q":"partial',
            },
        ]
    if not stream:
        return body

    async def chunks():
        for index, frame in enumerate(frames):
            data = json.dumps(frame, ensure_ascii=False)
            yield (data + '\n') if protocol == 'ollama' else ('data: ' + data + '\n\n')
            if (slow_continue and index == 0) or (slow_tools and index == len(frames) - 1):
                await asyncio.sleep(30)

    return StreamingResponse(
        chunks(), media_type='application/x-ndjson' if protocol == 'ollama' else 'text/event-stream'
    )
