"""Actual HTTP/socket/database failure, Continue and terminal archive recovery."""

import asyncio
import copy
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
    async with httpx.AsyncClient(
        base_url=API, trust_env=False, timeout=30, headers={'Authorization': 'Bearer ' + seed['owner']['token']}
    ) as c:

        async def req(method, path, body=None):
            res = await c.request(method, path, json=body)
            assert res.is_success, (res.status_code, res.text)
            return res.json()

        sock = socketio.AsyncClient(reconnection=False)
        queue = asyncio.Queue()

        @sock.on('events')
        async def capture(event):
            await queue.put(event)

        await sock.connect(
            API, auth={'token': seed['owner']['token']}, socketio_path='ws/socket.io', transports=['websocket']
        )

        async def done(mid):
            async def receive():
                while True:
                    event = await queue.get()
                    data = (event.get('data') or {}).get('data') or {}
                    if event.get('message_id') == mid and data.get('done') and 'output' in data:
                        return data

            return await asyncio.wait_for(receive(), 30)

        async def archive(cid, mid, status, details):
            bundle = await c.get('/api/v1/chats/' + cid + '/archive-bundle')
            assert bundle.is_success, bundle.text
            restored = await c.post(
                '/api/v1/archives/import',
                data={'import_id': str(uuid.uuid4())},
                files={'file': ('synthetic.zip', bundle.content, 'application/zip')},
            )
            assert restored.is_success, restored.text
            value = (await req('GET', '/api/v1/chats/' + restored.json()['id']))['chat']['history']['messages'][mid]
            assert value['response_status'] == status and value.get('incomplete_details') == details
            return value

        report = []
        try:
            for protocol, text in [('openai-chat', 'R75_FAILURE'), ('openai-responses', 'R74_RESPONSE_INCOMPLETE')]:
                model = seed['bindings'][protocol]['id']
                cid = (
                    await req(
                        'POST',
                        '/api/v1/chats/new',
                        {
                            'chat': {
                                'title': 'R75 HTTP synthetic',
                                'models': [model],
                                'history': {'currentId': None, 'messages': {}},
                            }
                        },
                    )
                )['id']
                uid, mid = str(uuid.uuid4()), str(uuid.uuid4())
                user = {
                    'id': uid,
                    'role': 'user',
                    'content': text,
                    'parentId': None,
                    'childrenIds': [mid],
                    'models': [model],
                }
                base = {
                    'chat_id': cid,
                    'model': model,
                    'stream': True,
                    'id': mid,
                    'session_id': sock.get_sid(),
                    'background_tasks': {},
                }
                await req(
                    'POST',
                    '/api/chat/completions',
                    {**base, 'oi_request_id': str(uuid.uuid4()), 'messages': [user], 'user_message': user},
                )
                final = await done(mid)
                message = (await req('GET', '/api/v1/chats/' + cid))['chat']['history']['messages'][mid]
                status = 'failed' if protocol == 'openai-chat' else 'incomplete'
                assert final['response_status'] == message['response_status'] == status
                assert len((await req('GET', f'/api/v1/chats/{cid}/usage'))['calls']) == 1
                assert not any(i.get('status') == 'pending' for i in message['output'])
                restored = await archive(cid, mid, status, message.get('incomplete_details'))
                if protocol == 'openai-chat':
                    assert message.get('error') and final.get('error') and restored.get('error')
                    assert 'R75 preserved partial.' in json.dumps(message['output'])
                    await req(
                        'POST',
                        '/api/chat/completions',
                        {
                            **base,
                            'oi_request_id': str(uuid.uuid4()),
                            'assistant_message_id': mid,
                            'messages': [user, copy.deepcopy(message)],
                        },
                    )
                    final = await done(mid)
                    message = (await req('GET', '/api/v1/chats/' + cid))['chat']['history']['messages'][mid]
                    assert 'error' in final and final['error'] is None and not message.get('error')
                    assert 'R75 preserved partial.' in json.dumps(
                        message['output']
                    ) and 'Native stage6 fixture.' in json.dumps(message['output'])
                    calls = (await req('GET', f'/api/v1/chats/{cid}/usage'))['calls']
                    assert [call['status'] for call in calls] == ['failed', 'completed']
                report.append(
                    {
                        'protocol': protocol,
                        'archive_terminal_preserved': True,
                        'failed_then_continue': protocol == 'openai-chat',
                        'message': message,
                    }
                )
            (ROOT / 'round13-http.json').write_text(json.dumps(report, indent=2) + '\n')
            print(
                'Round13 HTTP: failed-stream tool suppression, Continue error recovery, ledger history and terminal archive restore passed'
            )
        finally:
            await sock.disconnect()


asyncio.run(main())
