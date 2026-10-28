"""Synthetic provider for exact cost, repeated usage and tool-followup acceptance."""

import asyncio, json
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
events = []
mode = {'cost': '0.03'}


@app.get('/events')
async def observed():
    return events


@app.post('/mode')
async def configure(request: Request):
    global mode
    mode = await request.json()
    return mode


@app.get('/v1/models')
async def models():
    return {'data': [{'id': 'fixture'}]}


@app.post('/v1/chat/completions')
async def complete(request: Request):
    b = await request.json()
    m = dict(mode)
    events.append(b)
    tools = [t.get('function', {}).get('name') for t in b.get('tools', [])]
    is_tool = m.get('tool') and 'search_chats' in tools and not any(x.get('role') == 'tool' for x in b['messages'])
    text = 'Stage7 synthetic reply.'
    usage = {'prompt_tokens': 100, 'completion_tokens': 5}
    if m.get('cost') is not None:
        usage['cost'] = m['cost']
    tool = {
        'index': 0,
        'id': 'stage7-tool',
        'type': 'function',
        'function': {'name': 'search_chats', 'arguments': json.dumps({'query': 'stage7-nonexistent'})},
    }
    if not b.get('stream'):
        return {
            'id': 'fixture',
            'choices': [
                {
                    'index': 0,
                    'message': {'role': 'assistant', 'content': json.dumps({'title': 'Stage7 synthetic'})},
                    'finish_reason': 'stop',
                }
            ],
            'usage': usage,
        }

    async def stream():
        if m.get('delay'):
            await asyncio.sleep(m['delay'])
        if m.get('interrupted'):
            yield (
                'data: '
                + json.dumps(
                    {
                        'choices': [
                            {'index': 0, 'delta': {'content': 'Partial synthetic reply'}, 'finish_reason': None}
                        ],
                        'usage': {'prompt_tokens': 100},
                    }
                )
                + '\n\n'
            )
            return
        delta = {'tool_calls': [tool]} if is_tool else {'content': text}
        yield 'data: ' + json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}) + '\n\n'
        final = {
            'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if is_tool else 'stop'}],
            'usage': usage,
        }
        yield 'data: ' + json.dumps(final) + '\n\n'
        yield 'data: ' + json.dumps(final) + '\n\n'
        if m.get('cost') is not None:
            yield 'data: ' + json.dumps({'choices': [], 'usage': {'cost': m['cost']}}) + '\n\n'
        yield 'data: [DONE]\n\n'

    return StreamingResponse(stream(), media_type='text/event-stream')
