"""Real HTTP/socket/DB boundary for stream filters, tag projections and media."""

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import socketio

ROOT = Path(os.environ['OI_STAGE6_STATE'])
API = os.environ['OI_STAGE6_API']
PROVIDER = os.environ['OI_STAGE6_PROVIDER']


async def main():
    seed = json.loads((ROOT / 'seed.json').read_text())
    owner = seed['owner']
    async with httpx.AsyncClient(
        base_url=API, trust_env=False, timeout=30, headers={'Authorization': 'Bearer ' + owner['token']}
    ) as client:

        async def req(method, path, body=None):
            r = await client.request(method, path, json=body)
            r.raise_for_status()
            return r.json()

        function_id = 'round10_stream_fixture'
        code = """import json
class Filter:
    toggle = True
    async def stream(self, event):
        return json.loads(json.dumps(event).replace('round10-original', 'round10-filtered'))
"""
        await req(
            'POST',
            '/api/v1/functions/create',
            {'id': function_id, 'name': 'Round10 synthetic stream filter', 'content': code, 'meta': {}},
        )
        await req('POST', f'/api/v1/functions/id/{function_id}/toggle')
        await req('POST', f'/api/v1/functions/id/{function_id}/toggle/global')
        sock = socketio.AsyncClient(reconnection=False)
        queue = asyncio.Queue()

        @sock.on('events')
        async def event(value):
            if value.get('data', {}).get('type') == 'chat:completion' and value.get('data', {}).get('data', {}).get(
                'done'
            ):
                await queue.put(value)

        await sock.connect(API, auth={'token': owner['token']}, socketio_path='ws/socket.io', transports=['websocket'])
        report = []
        try:

            async def exercise(protocol, case, temporary=False):
                model = seed['bindings'][protocol]['id']
                cid = (
                    'local:' + sock.get_sid()
                    if temporary
                    else (
                        await req(
                            'POST',
                            '/api/v1/chats/new',
                            {
                                'chat': {
                                    'models': [model],
                                    'title': 'Round10 synthetic',
                                    'history': {'currentId': None, 'messages': {}},
                                }
                            },
                        )
                    )['id']
                )
                tid = 'round10-' + str(uuid.uuid4())
                messages = []
                parent = None
                first = None
                for turn in range(2):
                    uid, mid = str(uuid.uuid4()), str(uuid.uuid4())
                    user = {
                        'id': uid,
                        'role': 'user',
                        'content': 'Synthetic ' + (case if turn == 0 else 'next') + ' replay check.',
                        'parentId': parent,
                        'childrenIds': [mid],
                        'models': [model],
                    }
                    messages.append(user)
                    await req(
                        'POST',
                        '/api/chat/completions',
                        {
                            'model': model,
                            'stream': True,
                            'chat_id': cid,
                            'id': mid,
                            'parent_id': parent,
                            'session_id': sock.get_sid(),
                            'oi_temporary_id': tid,
                            'oi_request_id': str(uuid.uuid4()),
                            'messages': messages if temporary else [{'role': 'user', 'content': user['content']}],
                            'user_message': user,
                            'background_tasks': {},
                            'filter_ids': [function_id] if turn == 0 and case == 'round10_filter' else [],
                            'params': {'provider_options': {'fixture_case': case} if turn == 0 else {}},
                        },
                    )
                    value = await asyncio.wait_for(queue.get(), 30)
                    assert value['message_id'] == mid, value
                    data = value['data']['data']
                    assert not data.get('error'), data
                    if temporary:
                        assert data.get('oi_native_state')
                        answer = {
                            'id': mid,
                            'role': 'assistant',
                            'model': model,
                            'content': data.get('content', ''),
                            'output': data['output'],
                            'oi_native_state': data['oi_native_state'],
                            'parentId': uid,
                            'childrenIds': [],
                        }
                    else:
                        saved = await req('GET', '/api/v1/chats/' + cid)
                        answer = saved['chat']['history']['messages'][mid]
                        assert answer.get('oi_native') and 'consumer_output' in answer['oi_native'][0], answer
                    if turn == 0:
                        first = answer
                    messages.append(answer)
                    parent = mid
                captured = (await client.get(PROVIDER + '/events')).json()[-1]['body']
                payload = json.dumps(captured)
                (ROOT / f'round10-{protocol}-{case}-{temporary}.json').write_text(
                    json.dumps({'first': first, 'outbound': captured}, indent=2) + '\n'
                )
                if case == 'round10_filter':
                    assert 'round10-filtered' in payload and 'round10-original' not in payload
                elif case == 'round10_tags':
                    assert (
                        'round10-signature' in payload
                        and '<reason>a</reason>x<think>b</think>y<reason>c</reason>z' in payload
                    )
                else:
                    text = json.dumps(first['output'])
                    assert '/api/v1/files/' in text and 'data:image/png;base64,' not in text
                    assert 'A picture:' in payload
                report.append({'protocol': protocol, 'case': case, 'temporary': temporary, 'status': 'passed'})

            for protocol in seed['bindings']:
                await exercise(protocol, 'round10_filter')
            for temporary in [False, True]:
                await exercise('gemini', 'round10_tags', temporary)
            await exercise('gemini', 'round10_markdown')
        finally:
            await sock.disconnect()
            await req('POST', f'/api/v1/functions/id/{function_id}/toggle')
        (ROOT / 'round10-http.json').write_text(
            json.dumps({'scenarios': report, 'supplier': 'loopback synthetic only'}, indent=2) + '\n'
        )
        print(
            'Round10 HTTP: seven stream filters, saved/temporary partitioned history and real Markdown image storage passed'
        )


asyncio.run(main())
