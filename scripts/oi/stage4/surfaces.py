"""Synthetic stage4 integration scenario; run with scripts/oi/stage4-acceptance.py."""

import os
import asyncio, json, pathlib, uuid, time
import httpx

p = pathlib.Path(os.environ['OI_STAGE4_STATE'])
seed = json.loads((p / 'seed.json').read_text())
report = {}


async def main():
    async with httpx.AsyncClient(
        base_url=os.environ.get('OI_STAGE4_API', 'http://127.0.0.1:8094'),
        headers={'Authorization': 'Bearer ' + seed['owner']['token']},
        timeout=90,
    ) as c:

        async def call(method, path, body=None, **kwargs):
            r = await c.request(method, path, json=body, **kwargs)
            assert r.is_success, (path, r.status_code, r.text[:350])
            return r.json()

        async def persist():
            (p / 'surfaces-evidence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

        a, b = seed['bindings']['a_chat']['id'], seed['bindings']['b_chat']['id']
        await call(
            'POST',
            '/api/v1/configs/import',
            {
                'config': {
                    'task.title.enable': True,
                    'task.tags.enable': False,
                    'task.follow_up.enable': False,
                    'channels.enable': True,
                    'automations.enable': True,
                    'subagents.enable': True,
                }
            },
        )

        async def generate(model, chat=None, kind='assistant', extra=None):
            chat = chat or str(uuid.uuid4())
            mid = str(uuid.uuid4())
            uid = str(uuid.uuid4())
            await call('POST', '/api/v1/chats/drafts', {'chat_id': chat})
            body = {
                'model': model,
                'chat_id': chat,
                'id': mid,
                'oi_request_id': str(uuid.uuid4()),
                'stream': False,
                'messages': [{'role': 'user', 'content': 'Synthetic stage4 entrypoint check.'}],
                'user_message': {
                    'id': uid,
                    'role': 'user',
                    'content': 'Synthetic stage4 entrypoint check.',
                    'parentId': None,
                    'models': [model],
                },
                'background_tasks': {},
                'params': {'function_calling': 'legacy'},
                **(extra or {}),
            }
            response = await call('POST', '/api/chat/completions', body)
            assert 'error' not in response, response
            usage = await call('GET', f'/api/v1/chats/{chat}/usage')
            assert usage['calls'], usage
            return {'chat_id': chat, 'message_id': mid, 'usage': usage, 'response': response}

        first, second = await asyncio.gather(generate(a), generate(b))
        assert first['usage']['calls'][0]['binding_id'] == a and second['usage']['calls'][0]['binding_id'] == b
        report['M01'] = {'parallel_A': first['chat_id'], 'parallel_B': second['chat_id'], 'stable_bindings': [a, b]}
        await persist()
        # Persist user defaults/favorites and folder model selection, then re-open and dispatch it.
        await call('POST', '/api/v1/users/user/settings/update', {'ui': {'models': [b], 'pinnedModels': [a, b]}})
        settings = await call('GET', '/api/v1/users/user/settings')
        assert settings['ui']['models'] == [b] and settings['ui']['pinnedModels'] == [a, b]
        folder = await call(
            'POST', '/api/v1/folders/', {'name': 'Stage4 folder ' + uuid.uuid4().hex[:6], 'data': {'model_ids': [b]}}
        )
        saved = await call('GET', f'/api/v1/folders/{folder["id"]}')
        assert saved['data']['model_ids'] == [b]
        inherited = await generate(saved['data']['model_ids'][0], extra={'folder_id': folder['id']})
        assert inherited['usage']['calls'][0]['binding_id'] == b
        report['M04'] = {'saved_defaults': settings['ui']['models'], 'favorites': settings['ui']['pinnedModels']}
        report['M05'] = {'folder': folder['id'], 'chat': inherited['chat_id'], 'binding': b}
        await persist()
        # Explicitly permitted group preset with a second same-name binding.
        other = await call(
            'POST',
            '/api/v1/auths/add',
            {
                'name': 'Stage4 Reader',
                'email': f'stage4-reader-{uuid.uuid4().hex[:6]}@example.invalid',
                'password': 'stage4-synthetic-password',
                'role': 'user',
            },
        )
        login = await call(
            'POST', '/api/v1/auths/signin', {'email': other['email'], 'password': 'stage4-synthetic-password'}
        )
        seed['reader'] = login
        (p / 'seed.json').write_text(json.dumps(seed, indent=2))
        group = await call(
            'POST',
            '/api/v1/groups/create',
            {
                'name': 'Stage4 readers ' + uuid.uuid4().hex[:6],
                'description': 'Synthetic',
                'data': {'config': {'default_models': [b]}},
            },
        )
        await call('POST', f'/api/v1/groups/id/{group["id"]}/users/add', {'user_ids': [other['id']]})
        preset = 'stage4-preset-' + uuid.uuid4().hex[:8]
        await call(
            'POST',
            '/api/v1/models/create',
            {
                'id': preset,
                'base_model_id': b,
                'name': 'Stage4 Preset B',
                'params': {'temperature': 0.25},
                'meta': {'capabilities': {'builtin_tools': False}},
                'access_grants': [{'principal_type': 'group', 'principal_id': group['id'], 'permission': 'read'}],
            },
        )
        await call('GET', '/api/models')
        presetcall = await generate(preset)
        assert (
            presetcall['usage']['calls'][0]['binding_id'] == b and presetcall['usage']['calls'][0]['model_id'] == preset
        )
        async with httpx.AsyncClient(
            base_url=str(c.base_url), headers={'Authorization': 'Bearer ' + login['token']}, timeout=30
        ) as reader:
            visible = (await reader.get('/api/models')).json()['data']
            assert preset in {m['id'] for m in visible}
            r = await reader.post(
                '/api/chat/completions',
                json={
                    'model': preset,
                    'stream': False,
                    'messages': [{'role': 'user', 'content': 'Synthetic group permission'}],
                },
            )
            assert r.status_code == 200, r.text[:200]
            await call('POST', f'/api/v1/groups/id/{group["id"]}/users/remove', {'user_ids': [other['id']]})
            r = await reader.post(
                '/api/chat/completions',
                json={
                    'model': preset,
                    'stream': False,
                    'messages': [{'role': 'user', 'content': 'Synthetic revoked group'}],
                },
            )
            assert r.status_code in [400, 403, 404] and 'Model not found' in r.text, r.text[:200]
        report['M06'] = {'preset': preset, 'chat': presetcall['chat_id'], 'binding': b, 'temperature': 0.25}
        report['M09'] = {'group': group['id'], 'granted_call': 200, 'revoked_call': r.status_code}
        await persist()
        # Model-level controls override only the chosen binding on the outbound request.
        control = await generate(b, extra={'params': {'model_controls': {b: {'oi_reasoning': 'level_1'}}}})
        events = (await c.get(os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095') + '/events')).json()
        assert events[-1]['reasoning_effort'] == 'high', events[-1]
        report['M16'] = {'chat': control['chat_id'], 'binding': b, 'outbound': 'high'}
        await persist()
        # Background task configured B and Arena constrained to B.
        await call('POST', '/api/v1/configs/import', {'config': {'task.model.external': b}})
        task = await call(
            'POST',
            '/api/v1/tasks/title/completions',
            {'model': a, 'chat_id': first['chat_id'], 'messages': [{'role': 'user', 'content': 'Synthetic title'}]},
        )
        assert 'error' not in task
        usage = await call('GET', f'/api/v1/chats/{first["chat_id"]}/usage')
        assert any(x['binding_id'] == b for x in usage['calls'])
        report['M10'] = {'chat': first['chat_id'], 'selected_task_binding': b}
        await persist()
        await call('POST', '/api/v1/configs/import', {'config': {'task.model.external': ''}})
        print('Completed first HTTP entrypoint group', list(report))


asyncio.run(main())
