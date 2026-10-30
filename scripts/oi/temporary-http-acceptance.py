"""Real temporary HTTP/socket rounds with the loopback synthetic protocol fixture."""

import asyncio, json, uuid, os
from pathlib import Path
import httpx, socketio

ROOT = Path(os.environ['OI_STAGE6_STATE'])
API = os.environ['OI_STAGE6_API']
PROVIDER = os.environ['OI_STAGE6_PROVIDER']


async def main(stream):
    seed = json.loads((ROOT / 'seed.json').read_text())
    state = {
        'server_model': seed['bindings']['gemini']['id'],
        'login': {'email': 'oi-stage6-owner@example.invalid', 'password': 'stage6-synthetic-password'},
    }
    async with httpx.AsyncClient(base_url=API, trust_env=False, timeout=30) as c:
        r = await c.post('/api/v1/auths/signin', json=state['login'])
        r.raise_for_status()
        token = r.json()['token']
        c.headers['Authorization'] = 'Bearer ' + token
        sio = socketio.AsyncClient(reconnection=False)
        queue = asyncio.Queue()

        @sio.on('events')
        async def events(event):
            data = event.get('data', {})
            if data.get('type') == 'chat:completion' and data.get('data', {}).get('done'):
                await queue.put(event)

        await sio.connect(API, auth={'token': token}, socketio_path='ws/socket.io', transports=['websocket'])
        try:
            tid = 'r63-' + str(uuid.uuid4())
            chat = 'local:' + sio.get_sid()
            messages = []
            report = []
            for i in range(2):
                uid = str(uuid.uuid4())
                mid = str(uuid.uuid4())
                user = {
                    'id': uid,
                    'role': 'user',
                    'content': 'Synthetic native history check.',
                    'parentId': messages[-1]['id'] if messages else None,
                    'childrenIds': [mid],
                }
                messages.append(user)
                body = {
                    'model': state['server_model'],
                    'stream': stream,
                    'chat_id': chat,
                    'id': mid,
                    'session_id': sio.get_sid(),
                    'oi_temporary_id': tid,
                    'oi_request_id': str(uuid.uuid4()),
                    'messages': messages,
                    'user_message': user,
                    'background_tasks': {
                        'title_generation': False,
                        'tags_generation': False,
                        'follow_up_generation': False,
                    },
                }
                r = await c.post('/api/chat/completions', json=body)
                r.raise_for_status()
                event = await asyncio.wait_for(queue.get(), 30)
                data = event['data']['data']
                assert data.get('oi_native_state'), data.keys()
                message = {
                    'id': mid,
                    'role': 'assistant',
                    'model': state['server_model'],
                    'content': data.get('content', ''),
                    'output': data['output'],
                    'oi_native_state': data['oi_native_state'],
                    'parentId': uid,
                    'childrenIds': [],
                }
                messages.append(message)
                report.append({'round': i + 1, 'output_blocks': len(message['output']), 'capsule': True})
            observed = (await c.get(PROVIDER + '/events')).json()
            assert 'thoughtSignature' in json.dumps(observed[-1]['body']), observed[-1]
            form = {
                'oi_temporary_id': tid,
                'chat': {
                    'title': 'R63 temporary native acceptance',
                    'models': [state['server_model']],
                    'history': {'currentId': messages[-1]['id'], 'messages': {m['id']: m for m in messages}},
                    'messages': messages,
                },
            }
            saved = await c.post('/api/v1/chats/new', json=form)
            saved.raise_for_status()
            saved = saved.json()
            usage = await c.get('/api/v1/chats/' + saved['id'] + '/usage')
            usage.raise_for_status()
            usage = usage.json()
            assert usage['totals']['call_count'] == 2 and usage['totals']['unknown_calls'] == 2, usage['totals']
            assert saved['chat']['history']['messages'][messages[-1]['id']].get('oi_native')
            retry = await c.post('/api/v1/chats/new', json=form)
            retry.raise_for_status()
            assert retry.json()['id'] == saved['id']
            (ROOT / f'http-temporary-{stream}.json').write_text(
                json.dumps(
                    {
                        'rounds': report,
                        'signed_history_in_second_request': True,
                        'saved_id': saved['id'],
                        'totals': usage['totals'],
                        'idempotent_save': True,
                    },
                    indent=2,
                )
                + '\n'
            )
            print(
                'Two real HTTP/socket rounds preserve native signatures; atomic save retains two calls and is idempotent.'
            )
        finally:
            await sio.disconnect()


for stream in [False, True]:
    asyncio.run(main(stream))
