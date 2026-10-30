"""Loopback-only synthetic provider for stage4 acceptance; never send real data here."""

import asyncio
import base64
import io
import json
import wave
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5178'], allow_methods=['*'], allow_headers=['*'])
events = []
mode = 'normal'
png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y9bUAAAAASUVORK5CYII='


@app.get('/events')
async def observed():
    return events


@app.post('/mode')
async def set_mode(request: Request):
    global mode
    mode = (await request.json())['mode']
    return {'mode': mode}


@app.get('/{connection}/v1/models')
async def models(connection: str):
    return {'data': [{'id': 'same-model'}]}


def vector(text):
    text = str(text).lower()
    return [1.0, 0.0, 0.1] if 'alpha' in text else [0.0, 1.0, 0.1] if 'beta' in text else [0.1, 0.1, 1.0]


@app.post('/{connection}/v1/{operation:path}')
async def generate(connection: str, operation: str, request: Request):
    multipart = 'multipart/' in request.headers.get('content-type', '')
    body = dict(await request.form()) if multipart else await request.json()
    events.append(
        {
            'connection': connection,
            'operation': operation,
            'model': body.get('model'),
            'browser': bool(request.headers.get('origin')),
            'auth_valid': request.headers.get('authorization') == f'Bearer synthetic-{connection}',
            'reasoning_effort': body.get('reasoning_effort'),
            'temperature': body.get('temperature'),
        }
    )
    if body.get('model') == 'limited' or mode == operation + '_limited':
        return JSONResponse({'error': {'message': 'synthetic limited'}}, 429, headers={'retry-after': '7'})
    if operation == 'embeddings':
        inputs = body['input']
        inputs = inputs if isinstance(inputs, list) else [inputs]
        return {
            'data': [{'index': i, 'embedding': vector(text)} for i, text in enumerate(inputs)],
            'usage': {'prompt_tokens': len(inputs) * 8, 'total_tokens': len(inputs) * 8},
        }
    if operation == 'rerank':
        return {
            'results': [
                {'index': i, 'relevance_score': 0.95 if 'alpha' in str(doc).lower() else 0.1}
                for i, doc in enumerate(body['documents'])
            ],
            'usage': {'prompt_tokens': 9},
        }
    if operation == 'audio/speech':
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\0\0' * 100)
        return Response(stream.getvalue(), media_type='audio/wav')
    if operation == 'audio/transcriptions':
        return {'text': 'Synthetic alpha transcription', 'usage': {'input_tokens': 5, 'output_tokens': 3}}
    if operation.startswith('images/'):
        return {'data': [{'b64_json': png}], 'usage': {'input_tokens': 8, 'output_tokens': 4}}
    tool_names = [item.get('function', {}).get('name') for item in body.get('tools', []) if isinstance(item, dict)]
    events[-1]['delegate_available'] = 'delegate_task' in tool_names
    delegate = (
        'delegate_task' in tool_names
        and 'STAGE4_DELEGATE' in str(body.get('messages', []))
        and not any(item.get('role') == 'tool' for item in body.get('messages', []))
    )
    if delegate:
        call = {
            'index': 0,
            'id': 'synthetic-delegation',
            'type': 'function',
            'function': {
                'name': 'delegate_task',
                'arguments': json.dumps({'task': 'Synthetic child check', 'context': '', 'background': False}),
            },
        }

        async def tool_stream():
            yield (
                'data: '
                + json.dumps(
                    {
                        'choices': [{'index': 0, 'delta': {'tool_calls': [call]}, 'finish_reason': 'tool_calls'}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'cost': 0.001},
                    }
                )
                + '\n\n'
            )
            yield 'data: [DONE]\n\n'

        return StreamingResponse(tool_stream(), media_type='text/event-stream')
    text = f'Synthetic {connection} / {body.get("model")} reply.'
    usage = {'prompt_tokens': 10, 'completion_tokens': 5, 'cost': 0.001}
    if not body.get('stream'):
        return {
            'id': 'fixture',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
            'usage': usage,
        }

    async def stream():
        yield (
            'data: '
            + json.dumps({'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]})
            + '\n\n'
        )
        await asyncio.sleep(0.02)
        yield (
            'data: '
            + json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': usage})
            + '\n\n'
        )
        yield 'data: [DONE]\n\n'

    return StreamingResponse(stream(), media_type='text/event-stream')


@app.websocket('/{connection}/v1/realtime')
async def realtime(ws: WebSocket, connection: str):
    await ws.accept()
    await ws.send_json({'type': 'session.created', 'session': {'id': 'synthetic'}})
    i = 0
    try:
        while True:
            event = await ws.receive_json()
            kind = event.get('type')
            events.append(
                {
                    'connection': connection,
                    'operation': 'realtime',
                    'command': kind,
                    'model': ws.query_params.get('model'),
                    'auth_valid': ws.headers.get('authorization') == f'Bearer synthetic-{connection}',
                }
            )
            if kind == 'session.update':
                await ws.send_json({'type': 'session.updated'})
            elif kind == 'input_audio_buffer.append':
                i += 1
                item = f'item{i}'
                for event in [
                    {'type': 'input_audio_buffer.speech_started', 'item_id': item},
                    {'type': 'input_audio_buffer.committed', 'item_id': item},
                    {
                        'type': 'conversation.item.input_audio_transcription.completed',
                        'item_id': item,
                        'content_index': 0,
                        'transcript': 'synthetic',
                        'usage': {'input_tokens': 6, 'output_tokens': 2},
                    },
                ]:
                    await ws.send_json(event)
            elif kind == 'response.create':
                response = {
                    'id': f'response{i}',
                    'metadata': event.get('response', {}).get('metadata', {}),
                    'usage': {'input_tokens': 123},
                }
                await ws.send_json({'type': 'response.created', 'response': response})
                if mode == 'disconnect':
                    await ws.close(code=1011)
                    return
                if mode == 'hold':
                    continue
                await ws.send_json(
                    {
                        'type': 'response.done',
                        'response': {
                            **response,
                            'status': 'completed',
                            'usage': {'input_tokens': 123, 'output_tokens': 4},
                        },
                    }
                )
    except Exception:
        pass
