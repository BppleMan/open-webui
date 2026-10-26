"""Loopback test fault proxy. Never expose this control API outside an isolated test.

Rules affect only their method/path. Events contain method/path/status, never tokens,
request bodies or draft contents. Two proxy origins provide independent browser storage.
"""

import asyncio
import base64, hashlib, json
import contextlib
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

TARGET = os.environ.get('OI_STAGE5_TARGET', 'http://127.0.0.1:8096')
assert urlsplit(TARGET).hostname in {'127.0.0.1', 'localhost'}
rules = []
events = []


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        app.state.client = client
        yield


app = FastAPI(lifespan=lifespan)


@app.post('/__oi_test__/control')
async def control(request: Request):
    global rules
    assert request.client.host in {'127.0.0.1', '::1'}
    rules = (await request.json()).get('rules', [])
    return {'configured': len(rules)}


@app.get('/__oi_test__/events')
async def evidence():
    return events


@app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
async def forward(path: str, request: Request):
    record = {'method': request.method, 'path': '/' + path, 'status': None}
    # Non-secret identity/version evidence only; never retain the bearer credential.
    auth = request.headers.get('authorization', '').removeprefix('Bearer ')
    if auth:
        record['credential_hash'] = hashlib.sha256(auth.encode()).hexdigest()[:16]
        try:
            record['expires_at'] = json.loads(base64.urlsafe_b64decode(auth.split('.')[1] + '==')).get('exp')
        except Exception:
            pass
    events.append(record)
    rule = next(
        (
            r
            for r in rules
            if ('/' + path).startswith(r['path'])
            and request.method == r.get('method', request.method)
            and r.get('remaining', 1) > 0
        ),
        None,
    )
    if rule and 'remaining' in rule:
        rule['remaining'] -= 1
    body = await request.body()
    if rule and rule.get('phase', 'request') == 'request':
        await asyncio.sleep(rule.get('delay', 0))
    if rule and rule.get('status'):
        record['status'] = rule['status']
        return JSONResponse({'detail': 'Synthetic stage5 fault'}, status_code=rule['status'])
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in {'host', 'content-length', 'connection', 'accept-encoding'}
    }
    headers['host'] = request.headers['host']
    query = ('?' + request.url.query) if request.url.query else ''
    upstream = await request.app.state.client.send(
        request.app.state.client.build_request(
            request.method, TARGET + '/' + path + query, headers=headers, content=body
        ),
        stream=True,
    )
    if rule and rule.get('phase') == 'response':
        await asyncio.sleep(rule.get('delay', 0))
    record['status'] = upstream.status_code

    async def stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    response = StreamingResponse(stream(), status_code=upstream.status_code)
    response.raw_headers = [
        (k, v) for k, v in upstream.headers.raw if k.lower() not in {b'connection', b'transfer-encoding'}
    ]
    return response


@app.websocket('/{path:path}')
async def socket(ws: WebSocket, path: str):
    await ws.accept()
    query = ('?' + ws.url.query) if ws.url.query else ''
    try:
        async with websockets.connect(
            TARGET.replace('http:', 'ws:') + '/' + path + query, max_size=16 * 1024 * 1024
        ) as upstream:

            async def inbound():
                while True:
                    message = await ws.receive()
                    if message['type'] == 'websocket.disconnect':
                        return
                    await upstream.send(message.get('text') if message.get('text') is not None else message['bytes'])

            async def outbound():
                async for message in upstream:
                    if isinstance(message, str):
                        await ws.send_text(message)
                    else:
                        await ws.send_bytes(message)

            tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        with contextlib.suppress(Exception):
            await ws.close(code=1011)
