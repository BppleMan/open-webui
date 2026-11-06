"""Real socket interruption and cancel-publication ordering through HTTP/WS/storage."""

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
TOOL = '''import asyncio
class Tools:
    async def r76_probe(self, q: str, __event_emitter__=None) -> str:
        """Synthetic local result and cancellable wait; no external actions."""
        if q == '2':
            await __event_emitter__({'type':'status','data':{'description':'R76 second tool started','done':False}})
            await asyncio.sleep(30)
        return 'R76 result:'+q
'''


async def main():
    seed = json.loads((ROOT / 'seed.json').read_text())
    async with httpx.AsyncClient(
        base_url=API, trust_env=False, timeout=30, headers={'Authorization': 'Bearer ' + seed['owner']['token']}
    ) as c:

        async def req(method, path, body=None):
            r = await c.request(method, path, json=body)
            assert r.is_success, (r.status_code, r.text)
            return r.json()

        tool_id = 'r76_synthetic_tool'
        await req(
            'POST', '/api/v1/tools/create', {'id': tool_id, 'name': 'R76 synthetic tool', 'content': TOOL, 'meta': {}}
        )
        sock = socketio.AsyncClient(reconnection=False)
        queue = asyncio.Queue()

        @sock.on('events')
        async def capture(event):
            await queue.put(event)

        await sock.connect(
            API, auth={'token': seed['owner']['token']}, socketio_path='ws/socket.io', transports=['websocket']
        )

        async def wait(mid, predicate):
            async def receive():
                while True:
                    event = await queue.get()
                    body = event.get('data') or {}
                    if event.get('message_id') == mid and predicate(body):
                        return body

            return await asyncio.wait_for(receive(), 30)

        async def ask(cid, model, text, parent=None, history=None, tools=False):
            uid, mid = str(uuid.uuid4()), str(uuid.uuid4())
            user = {
                'id': uid,
                'role': 'user',
                'content': text,
                'parentId': parent,
                'childrenIds': [mid],
                'models': [model],
            }
            await req(
                'POST',
                '/api/chat/completions',
                {
                    'chat_id': cid,
                    'model': model,
                    'stream': True,
                    'id': mid,
                    'parent_id': parent,
                    'session_id': sock.get_sid(),
                    'oi_request_id': str(uuid.uuid4()),
                    'messages': [*copy.deepcopy(history or []), user],
                    'user_message': user,
                    'background_tasks': {},
                    'tool_ids': [tool_id] if tools else [],
                    'params': {'function_calling': 'native'},
                },
            )
            return mid, user

        report = []
        try:
            model = seed['bindings']['openai-chat']['id']
            for scenario in ['R76_BROKEN_TOOL', 'R76_CANCEL_TOOLS']:
                cid = (
                    await req(
                        'POST',
                        '/api/v1/chats/new',
                        {
                            'chat': {
                                'title': scenario,
                                'models': [model],
                                'history': {'currentId': None, 'messages': {}},
                            }
                        },
                    )
                )['id']
                mid, user = await ask(cid, model, scenario, tools=scenario == 'R76_CANCEL_TOOLS')
                if scenario == 'R76_CANCEL_TOOLS':
                    await wait(mid, lambda e: 'R76 second tool started' in json.dumps(e))
                    await req('POST', '/api/tasks/chat/' + quote(cid, safe='') + '/stop')
                    terminal = (await wait(mid, lambda e: e.get('type') == 'chat:tasks:cancel'))['data']
                else:
                    terminal = (
                        await wait(mid, lambda e: e.get('data', {}).get('done') and 'output' in e.get('data', {}))
                    )['data']
                    assert terminal.get('error')
                # Read immediately when the notification releases the queue, before any delay.
                saved = (await req('GET', '/api/v1/chats/' + cid))['chat']['history']['messages'][mid]
                assert saved['done'] and saved['output'] == terminal['output']
                results = [i for i in saved['output'] if i.get('type') == 'function_call_output']
                assert len(results) == (1 if scenario == 'R76_CANCEL_TOOLS' else 0)
                assert all('consumer_output' in n for n in saved['oi_native'])
                next_id, _ = await ask(cid, model, 'R76_FOLLOWUP', parent=mid, history=[user, saved])
                after = (
                    await wait(next_id, lambda e: e.get('data', {}).get('done') and 'output' in e.get('data', {}))
                )['data']
                assert not after.get('error') and 'Native stage6 fixture.' in json.dumps(after['output'])
                payload = (await c.get(PROVIDER + '/events')).json()[-1]['body']
                if results:
                    assert 'R76 result:1' in json.dumps(payload) and 'R76 result:2' not in json.dumps(payload)
                else:
                    assert 'R76 kept partial.' in json.dumps(payload) and 'unfinished' not in json.dumps(payload)
                report.append(
                    {
                        'scenario': scenario,
                        'saved_before_notification': True,
                        'stored_results': len(results),
                        'followup': 'passed',
                        'message': saved,
                        'outbound': payload,
                    }
                )
            (ROOT / 'round14-http.json').write_text(json.dumps(report, indent=2) + '\n')
            print(
                'Round14 HTTP: midstream socket failure replay and immediate followup after saved cancellation passed'
            )
        finally:
            await sock.disconnect()
            await req('DELETE', '/api/v1/tools/id/' + tool_id + '/delete')


asyncio.run(main())
