"""Synthetic stage4 integration scenario; run with scripts/oi/stage4-acceptance.py."""

import os
import asyncio, json, pathlib, uuid, time
import httpx

p = pathlib.Path(os.environ['OI_STAGE4_STATE'])
seed = json.loads((p / 'seed.json').read_text())
report = json.loads((p / 'surfaces-evidence.json').read_text())


async def main():
    async with httpx.AsyncClient(
        base_url=os.environ.get('OI_STAGE4_API', 'http://127.0.0.1:8094'),
        headers={'Authorization': 'Bearer ' + seed['owner']['token']},
        timeout=90,
    ) as c:

        async def call(method, path, body=None):
            r = await c.request(method, path, json=body)
            assert r.is_success, (path, r.status_code, r.text[:300])
            return r.json()

        def save():
            (p / 'surfaces-evidence.json').write_text(json.dumps(report, indent=2))

        a, b = seed['bindings']['a_chat']['id'], seed['bindings']['b_chat']['id']
        parent = report['M01']['parallel_A']
        await call('POST', '/api/v1/configs/import', {'config': {'task.model.external': b}})
        await call(
            'POST',
            '/api/v1/tasks/title/completions',
            {'model': a, 'chat_id': parent, 'messages': [{'role': 'user', 'content': 'Synthetic title'}]},
        )
        usage = await call('GET', f'/api/v1/chats/{parent}/usage')
        assert any(x['binding_id'] == b for x in usage['calls'])
        report['M10'] = {'chat': parent, 'configured_task_binding': b}
        save()
        await call('POST', '/api/v1/configs/import', {'config': {'task.model.external': ''}})
        arena = 'stage4-arena'
        await call(
            'POST',
            '/api/v1/evaluations/config',
            {
                'ENABLE_EVALUATION_ARENA_MODELS': True,
                'EVALUATION_ARENA_MODELS': [
                    {
                        'id': arena,
                        'name': 'Stage4 Arena B',
                        'meta': {'model_ids': [b], 'filter_mode': 'include', 'access_grants': []},
                    }
                ],
            },
        )
        saved = await call('GET', '/api/v1/evaluations/config')
        assert saved['EVALUATION_ARENA_MODELS'][0]['meta']['model_ids'] == [b]
        await call('GET', '/api/models')
        cid = str(uuid.uuid4())
        await call('POST', '/api/v1/chats/drafts', {'chat_id': cid})
        result = await call(
            'POST',
            '/api/chat/completions',
            {
                'model': arena,
                'chat_id': cid,
                'id': str(uuid.uuid4()),
                'oi_request_id': str(uuid.uuid4()),
                'stream': False,
                'messages': [{'role': 'user', 'content': 'Synthetic arena member'}],
                'params': {'function_calling': 'legacy'},
                'background_tasks': {},
            },
        )
        usage = await call('GET', f'/api/v1/chats/{cid}/usage')
        assert usage['calls'][0]['binding_id'] == b
        report['M12'] = {'arena': arena, 'chat': cid, 'actual_member': b}
        save()
        automation = await call(
            'POST',
            '/api/v1/automations/create',
            {
                'name': 'Stage4 Scheduled B',
                'is_active': False,
                'data': {
                    'prompt': 'Synthetic automation execution',
                    'model_id': b,
                    'rrule': 'FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1',
                },
            },
        )
        reopened = await call('GET', f'/api/v1/automations/{automation["id"]}')
        assert reopened['data']['model_id'] == b
        await call('POST', f'/api/v1/automations/{automation["id"]}/run')
        runs = []
        for _ in range(80):
            runs = await call('GET', f'/api/v1/automations/{automation["id"]}/runs')
            if runs and runs[0].get('status') in ['completed', 'success', 'failed']:
                break
            await asyncio.sleep(0.25)
        assert runs and runs[0]['status'] in ['completed', 'success'], runs
        report['M11'] = {'automation': automation['id'], 'runs': runs}
        save()
        channel = await call(
            'POST',
            '/api/v1/channels/create',
            {'name': 'Stage4 channel', 'type': 'group', 'user_ids': [seed['owner']['id']]},
        )
        message = await call(
            'POST',
            f'/api/v1/channels/{channel["id"]}/messages/post',
            {'content': f'<@M:{b}|Same chat> Synthetic channel reply', 'data': {}},
        )
        found = []
        for _ in range(80):
            found = await call('GET', f'/api/v1/channels/{channel["id"]}/messages/{message["id"]}/thread')
            if 'Synthetic b / same-model reply.' in str(found):
                break
            await asyncio.sleep(0.25)
        assert 'Synthetic b / same-model reply.' in str(found), found
        report['M13'] = {
            'channel': channel['id'],
            'parent_message': message['id'],
            'binding': b,
            'persisted_reply': True,
        }
        save()
        note = await call(
            'POST',
            '/api/v1/notes/create',
            {'title': 'Synthetic note', 'data': {'content': {'md': 'Synthetic alpha note'}}},
        )
        assert (await call('GET', f'/api/v1/notes/{note["id"]}'))['id'] == note['id']
        note_result = await call(
            'POST',
            '/api/chat/completions',
            {
                'model': b,
                'stream': False,
                'oi_temporary_id': 'note:' + note['id'],
                'messages': [{'role': 'user', 'content': 'Synthetic note title'}],
            },
        )
        assert 'Synthetic b' in str(note_result)
        report['M14'] = {'note': note['id'], 'inherited_binding': b, 'actual_call': True}
        save()
        print('HTTP batch passed', list(report))


asyncio.run(main())
