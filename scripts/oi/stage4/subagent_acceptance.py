"""Synthetic stage4 integration scenario; run with scripts/oi/stage4-acceptance.py."""

import os
import asyncio, json, pathlib, uuid, sqlite3
import httpx

p = pathlib.Path(os.environ['OI_STAGE4_STATE'])
seed = json.loads((p / 'seed.json').read_text())


async def main():
    async with httpx.AsyncClient(
        base_url=os.environ.get('OI_STAGE4_API', 'http://127.0.0.1:8094'),
        headers={'Authorization': 'Bearer ' + seed['owner']['token']},
        timeout=90,
    ) as c:

        async def call(method, path, body=None):
            r = await c.request(method, path, json=body)
            assert r.is_success, (path, r.status_code, r.text[:250])
            return r.json()

        await call(
            'POST',
            '/api/v1/configs/import',
            {'config': {'subagents.enable': True, 'task.model.external': '', 'task.title.enable': False}},
        )
        cid, mid, uid = [str(uuid.uuid4()) for _ in range(3)]
        b = seed['bindings']['b_chat']['id']
        await call('POST', '/api/v1/chats/drafts', {'chat_id': cid})
        r = await c.post(
            '/api/chat/completions',
            json={
                'model': b,
                'chat_id': cid,
                'id': mid,
                'oi_request_id': str(uuid.uuid4()),
                'session_id': 'stage4-synthetic-test',
                'stream': True,
                'messages': [{'role': 'user', 'content': 'STAGE4_DELEGATE synthetic test'}],
                'user_message': {'id': uid, 'role': 'user', 'content': 'STAGE4_DELEGATE synthetic test', 'models': [b]},
                'params': {'function_calling': 'native'},
                'background_tasks': {},
            },
        )
        assert r.status_code == 200, r.text[:200]
        saved = None
        for _ in range(120):
            saved = await call('GET', f'/api/v1/chats/{cid}')
            message = saved['chat'].get('history', {}).get('messages', {}).get(mid, {})
            if message.get('done') and 'Synthetic b / same-model reply.' in str(message):
                break
            await asyncio.sleep(0.25)
        assert message.get('done') and 'Synthetic b / same-model reply.' in str(message), message
        with sqlite3.connect(p / 'data/webui.db') as db:
            children = [
                {'id': row[0], 'meta': json.loads(row[1])}
                for row in db.execute('select id,meta from chat where meta is not null')
                if json.loads(row[1] or '{}').get('parent_chat_id') == cid
            ]
            assert len(children) == 1, children
            child = children[0]['id']
            calls = db.execute('select binding_id,usage from oi_model_call where chat_id=?', (child,)).fetchall()
            assert calls and all(row[0] == b for row in calls), calls
            assert all(json.loads(row[1])['context']['purpose'] == 'subagent' for row in calls), calls
        parent = await call('GET', f'/api/v1/chats/{cid}/usage')
        assert len(parent['calls']) == 2 and all(x['binding_id'] == b for x in parent['calls']), parent
        out = {
            'parent_chat_id': cid,
            'child_chat_id': child,
            'binding': b,
            'parent_calls': 2,
            'child_calls': len(calls),
            'child_purpose': 'subagent',
            'parent_relationship_preserved': True,
        }
        (p / 'subagent-evidence.json').write_text(json.dumps(out, indent=2))
        print(out)


asyncio.run(main())
