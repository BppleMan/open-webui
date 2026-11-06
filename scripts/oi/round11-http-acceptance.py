"""Actual HTTP/socket cancellation, interactive answer and authenticated temporary replay."""

import asyncio
import copy
import json
import os
import uuid
from pathlib import Path
from urllib.parse import quote

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
    ) as c:

        async def req(method, path, data=None):
            r = await c.request(method, path, json=data)
            r.raise_for_status()
            return r.json()

        sock = socketio.AsyncClient(reconnection=False)
        queue = asyncio.Queue()

        @sock.on('events')
        async def event(value):
            await queue.put(value)

        await sock.connect(API, auth={'token': owner['token']}, socketio_path='ws/socket.io', transports=['websocket'])

        async def wait(mid, predicate):
            async def receive():
                while True:
                    value = await queue.get()
                    if value.get('message_id') == mid and predicate(value.get('data') or {}):
                        return value['data']

            return await asyncio.wait_for(receive(), 20)

        async def new_chat(model):
            return (
                await req(
                    'POST',
                    '/api/v1/chats/new',
                    {
                        'chat': {
                            'title': 'R73 synthetic acceptance',
                            'models': [model],
                            'history': {'currentId': None, 'messages': {}},
                        }
                    },
                )
            )['id']

        async def ask(
            cid, model, text, parent=None, options=None, mid=None, temporary=None, history=None, continuing=False
        ):
            mid = mid or str(uuid.uuid4())
            uid = str(uuid.uuid4())
            user = {
                'id': uid,
                'role': 'user',
                'content': text,
                'parentId': parent,
                'childrenIds': [mid],
                'models': [model],
            }
            messages = copy.deepcopy(history or [])
            if not continuing:
                messages.append(user)
            body = {
                'chat_id': cid,
                'model': model,
                'stream': True,
                'id': mid,
                'parent_id': parent,
                'session_id': sock.get_sid(),
                'oi_request_id': str(uuid.uuid4()),
                'messages': messages,
                'background_tasks': {},
                'params': {'provider_options': options or {}},
            }
            if continuing:
                body['assistant_message_id'] = mid
            else:
                body['user_message'] = user
            if temporary:
                body['oi_temporary_id'] = temporary
            await req('POST', '/api/chat/completions', body)
            return mid, user

        done = lambda event: event.get('type') == 'chat:completion' and event.get('data', {}).get('done')
        try:
            for protocol in ['openai-chat', 'openai-responses']:
                model = seed['bindings'][protocol]['id']
                cid = await new_chat(model)
                mid, _ = await ask(cid, model, 'Synthetic partial tool', options={'fixture_case': 'r73_partial_tool'})
                await wait(mid, lambda e: e.get('data', {}).get('delta') == '{"q":"partial')
                await req('POST', '/api/tasks/chat/' + quote(cid, safe='') + '/stop')
                await wait(mid, lambda e: e.get('type') == 'chat:tasks:cancel')
                follow, _ = await ask(cid, model, 'Synthetic followup', parent=mid)
                await wait(follow, done)
                payload = json.dumps((await c.get(PROVIDER + '/events')).json()[-1]['body'])
                assert 'saved-prefix' in payload and 'r73-partial' not in payload
            model = seed['bindings']['gemini']['id']
            cid = await new_chat(model)
            mid, _ = await ask(cid, model, 'R73_ASK_USER')
            pending = await wait(
                mid,
                lambda e: any(
                    i.get('name') == 'ask_user' and i.get('status') == 'pending'
                    for i in e.get('data', {}).get('output', [])
                ),
            )
            call = next(i for i in pending['data']['output'] if i.get('name') == 'ask_user')
            await req(
                'POST',
                f'/api/v1/chats/{cid}/messages/{mid}/resolve',
                {'call_id': call['call_id'], 'action': 'answer', 'answers': {'route': 'North'}},
            )
            await wait(mid, done)
            payload = json.dumps((await c.get(PROVIDER + '/events')).json()[-1]['body'])
            assert 'r73-ask-signature' in payload and 'North' in payload
            retry = await c.post(
                f'/api/v1/chats/{cid}/messages/{mid}/resolve',
                json={'call_id': call['call_id'], 'action': 'answer', 'answers': {'route': 'North'}},
            )
            assert retry.status_code == 409
            tid = 'r73-' + str(uuid.uuid4())
            cid = 'local:' + sock.get_sid()
            mid, user = await ask(cid, model, 'R73_TEMPORARY_NATIVE', temporary=tid)
            first = (await wait(mid, done))['data']
            assert first.get('oi_native_state')
            message = {
                'id': mid,
                'role': 'assistant',
                'model': model,
                'parentId': user['id'],
                'childrenIds': [],
                'output': first['output'],
                'oi_native_state': first['oi_native_state'],
            }
            history = [user, message]
            await ask(cid, model, '', mid=mid, temporary=tid, history=history, continuing=True)
            await wait(mid, lambda e: 'R73 partial continuation.' in json.dumps(e))
            await req('POST', '/api/tasks/chat/' + quote(cid, safe='') + '/stop')
            cancel = (await wait(mid, lambda e: e.get('type') == 'chat:tasks:cancel'))['data']
            assert cancel.get('oi_native_state') and cancel['oi_native_state'] != first['oi_native_state']
            message.update(output=cancel['output'], oi_native_state=cancel['oi_native_state'])
            follow, _ = await ask(cid, model, 'R73 temporary followup', parent=mid, temporary=tid, history=history)
            await wait(follow, done)
            event = (await c.get(PROVIDER + '/events')).json()[-1]
            payload = json.dumps(event['body'])
            assert (
                'R73 initial reply.' in payload
                and 'R73 partial continuation.' in payload
                and 'thoughtSignature' in payload
            )
            (ROOT / 'round11-http.json').write_text(
                json.dumps(
                    {
                        'partial_tool_cancel_saved_history': ['openai-chat', 'openai-responses'],
                        'ask_user_answer_and_duplicate_rejection': 'passed',
                        'temporary_continue_cancel_capsule_and_prior_signature': 'passed',
                        'supplier': 'loopback synthetic only',
                        'final_outbound': event['body'],
                    },
                    indent=2,
                )
                + '\n'
            )
            print('Round11 HTTP cancellation, ask_user answer and temporary Continue replay passed')
        finally:
            await sock.disconnect()


asyncio.run(main())
