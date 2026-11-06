"""Saved/temporary parent transfer, side ledger isolation and authenticated temporary copies."""

import asyncio, copy, json, os, uuid, sqlite3
from pathlib import Path
import httpx, socketio

ROOT = Path(os.environ['OI_STAGE6_STATE'])
API = os.environ['OI_STAGE6_API']
PROVIDER = os.environ['OI_STAGE6_PROVIDER']


def portable(message):
    return {k: copy.deepcopy(v) for k, v in message.items() if k != 'oi_native'}


async def main():
    seed = json.loads((ROOT / 'seed.json').read_text())
    async with httpx.AsyncClient(
        base_url=API, trust_env=False, timeout=30, headers={'Authorization': 'Bearer ' + seed['owner']['token']}
    ) as c:

        async def req(method, path, body=None):
            r = await c.request(method, path, json=body)
            assert r.is_success, (r.status_code, r.text)
            return r.json()

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
                    e = await queue.get()
                    d = (e.get('data') or {}).get('data') or {}
                    if e.get('message_id') == mid and d.get('done') and 'output' in d:
                        return d

            return await asyncio.wait_for(receive(), 30)

        model = seed['bindings']['gemini']['id']

        async def ask(cid, text, history=None, temporary_id=None, side=False):
            uid, mid = str(uuid.uuid4()), str(uuid.uuid4())
            user = {
                'id': uid,
                'role': 'user',
                'content': text,
                'parentId': history[-1]['id'] if history else None,
                'childrenIds': [mid],
                'models': [model],
            }
            body = {
                'model': model,
                'stream': True,
                'chat_id': cid,
                'id': mid,
                'session_id': sock.get_sid(),
                'oi_request_id': str(uuid.uuid4()),
                'messages': [*copy.deepcopy(history or []), user],
                'user_message': user,
                'background_tasks': {},
            }
            if temporary_id:
                body['oi_temporary_id'] = temporary_id
            if side:
                body['oi_side_chat'] = True
                body['messages'].insert(
                    0,
                    {
                        'role': 'system',
                        'content': 'This is a separate side conversation. Earlier messages are reference context only. Answer the side question; do not continue the parent task.',
                    },
                )
            await req('POST', '/api/chat/completions', body)
            final = await done(mid)
            assert not final.get('error'), final
            return user, {
                'id': mid,
                'role': 'assistant',
                'model': model,
                'parentId': uid,
                'childrenIds': [],
                'done': True,
                **final,
            }

        report = []
        try:
            for temporary in [False, True]:
                parent_temp = str(uuid.uuid4()) if temporary else None
                parent_id = (
                    f'local:{sock.get_sid()}:{uuid.uuid4()}'
                    if temporary
                    else (
                        await req(
                            'POST',
                            '/api/v1/chats/new',
                            {
                                'chat': {
                                    'title': 'R77 parent',
                                    'models': [model],
                                    'history': {'currentId': None, 'messages': {}},
                                }
                            },
                        )
                    )['id']
                )
                first_user, parent = await ask(parent_id, 'R77_PARENT', temporary_id=parent_temp)
                parent_before = None
                if not temporary:
                    parent_before = (await req('GET', '/api/v1/chats/' + parent_id))['chat']
                    parent = portable(parent_before['history']['messages'][parent['id']])
                else:
                    assert parent.get('oi_native_state')
                source_parent = copy.deepcopy(parent)
                context = [first_user, parent]
                side_temp = str(uuid.uuid4())
                side_id = f'local:{sock.get_sid()}:{uuid.uuid4()}'
                transferred = await req(
                    'POST',
                    '/api/v1/side-chats/context',
                    {
                        'temporary_id': side_temp,
                        'messages': context,
                        **({'source_temporary_id': parent_temp} if temporary else {'source_chat_id': parent_id}),
                    },
                )
                for patch in transferred['messages']:
                    next(m for m in context if m['id'] == patch['id']).update(patch)
                assert parent.get('oi_native_state') and not parent.get('oi_native_invalidated')
                u1, a1 = await ask(side_id, 'R77_SIDE_FIRST', context, side_temp, True)
                first_out = (await c.get(PROVIDER + '/events')).json()[-1]['body']
                assert 'R77_PARENT-signature' in json.dumps(first_out)
                u2, a2 = await ask(side_id, 'R77_SIDE_NEXT', [*context, u1, a1], side_temp, True)
                next_out = (await c.get(PROVIDER + '/events')).json()[-1]['body']
                assert all(x in json.dumps(next_out) for x in ['R77_PARENT-signature', 'R77_SIDE_FIRST-signature'])
                totals = await req('GET', f'/api/v1/chats/temporary:{side_temp}/usage')
                assert len(totals['calls']) == 2
                parent_identity = 'temporary:' + parent_temp if temporary else parent_id
                assert len((await req('GET', f'/api/v1/chats/{parent_identity}/usage'))['calls']) == 1
                if not temporary:
                    assert (await req('GET', '/api/v1/chats/' + parent_id))['chat']['history'] == parent_before[
                        'history'
                    ]
                else:
                    # Copy source keeps its ORIGINAL parent capsule, not the transferred side capsule.
                    original = source_parent
                    destination = {
                        **copy.deepcopy(original),
                        'id': str(uuid.uuid4()),
                        'oi_source_message_id': original['id'],
                    }
                    patch = await req(
                        'POST',
                        '/api/v1/native-state/copy',
                        {'temporary_id': parent_temp, 'source': original, 'destination': destination},
                    )
                    destination.update(patch)
                    assert destination['oi_native_state'] and not destination['oi_native_invalidated']
                    assert len((await req('GET', f'/api/v1/chats/{parent_identity}/usage'))['calls']) == 1
                    await ask(parent_id, 'R77_SIDE_NEXT', [first_user, destination], parent_temp)
                    copied_out = (await c.get(PROVIDER + '/events')).json()[-1]['body']
                    assert 'R77_PARENT-signature' in json.dumps(copied_out)
                    assert len((await req('GET', f'/api/v1/chats/{parent_identity}/usage'))['calls']) == 2
                assert (await c.get('/api/v1/chats/' + side_id)).status_code in {401, 403, 404}
                with sqlite3.connect(ROOT / 'data/webui.db') as stored:
                    assert stored.execute('select count(*) from chat where id=?', (side_id,)).fetchone()[0] == 0
                    assert (
                        stored.execute('select count(*) from chat_message where chat_id=?', (side_id,)).fetchone()[0]
                        == 0
                    )
                    assert (
                        stored.execute(
                            "select count(*) from chat where instr(cast(chat as text),'R77_SIDE_') > 0"
                        ).fetchone()[0]
                        == 0
                    )
                    assert (
                        stored.execute(
                            "select count(*) from chat_message where instr(coalesce(content,'') || coalesce(cast(output as text),''),'R77_SIDE_') > 0"
                        ).fetchone()[0]
                        == 0
                    )
                report.append(
                    {
                        'temporary_parent': temporary,
                        'parent_unchanged': True,
                        'side_calls': 2,
                        'parent_calls': 1,
                        'native_parent_and_side_signatures': True,
                    }
                )
            (ROOT / 'upstream-v0130-http.json').write_text(json.dumps(report, indent=2) + '\n')
            print(
                'v0.13.0 HTTP: saved/temporary parent native transfer, side followup, isolated ledgers and no side Chat rows passed'
            )
        finally:
            await sock.disconnect()


asyncio.run(main())
