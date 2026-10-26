"""Synthetic stage4 integration scenario; run with scripts/oi/stage4-acceptance.py."""

import os
import asyncio, io, json, pathlib, sqlite3, uuid, wave, zipfile
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

        async def call(method, path, body=None, **kw):
            r = await c.request(method, path, json=body, **kw)
            assert r.is_success, (path, r.status_code, r.text[:250])
            return r.json()

        async def events():
            return (await c.get(os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095') + '/events')).json()

        def save():
            (p / 'remaining-evidence.json').write_text(json.dumps(report, indent=2))

        a, b = seed['bindings']['a_chat']['id'], seed['bindings']['b_chat']['id']
        ac, bc = seed['bindings']['a_cap']['id'], seed['bindings']['b_cap']['id']
        cid = str(uuid.uuid4())
        await call('POST', '/api/v1/chats/drafts', {'chat_id': cid})
        uid = str(uuid.uuid4())
        mid = str(uuid.uuid4())
        for kind in ['assistant', 'regenerate', 'continue']:
            if kind != 'continue':
                mid = str(uuid.uuid4())
            body = {
                'model': b,
                'chat_id': cid,
                'id': mid,
                'oi_request_id': str(uuid.uuid4()),
                'stream': False,
                'messages': [{'role': 'user', 'content': 'Synthetic stage4 ' + kind}],
                'user_message': {'id': uid, 'role': 'user', 'content': 'Synthetic stage4', 'models': [b]},
                'params': {'function_calling': 'legacy'},
                'background_tasks': {},
                'oi_generation_kind': kind,
            }
            if kind == 'continue':
                body['messages'].append({'role': 'assistant', 'content': 'Synthetic b / same-model reply.'})
                body['assistant_message_id'] = mid
            await call('POST', '/api/chat/completions', body)
        await call(
            'POST',
            '/api/v1/tasks/moa/completions',
            {
                'model': a,
                'prompt': 'Synthetic merge',
                'responses': ['synthetic A', 'synthetic B'],
                'chat_id': cid,
                'stream': False,
            },
        )
        usage = await call('GET', f'/api/v1/chats/{cid}/usage')
        assert len(usage['calls']) == 4 and sorted(x['binding_id'] for x in usage['calls']) == sorted([b, b, b, a]), (
            usage
        )
        report['M03'] = {'chat': cid, 'calls': 4, 'regen_continue_binding': b, 'merge_binding': a}
        save()
        # Native model toggles are scoped to the chosen binding, independent of identical upstream names.
        bindings = await call('GET', '/api/v1/model-bindings')
        bb = next(x for x in bindings if x['id'] == b)
        await call('PUT', f'/api/v1/model-bindings/{b}', {**bb, 'enabled': False})
        visible = (await call('GET', '/api/models'))['data']
        assert b not in {m['id'] for m in visible} and a in {m['id'] for m in visible}
        n = len(await events())
        r = await c.post(
            '/api/chat/completions', json={'model': b, 'messages': [{'role': 'user', 'content': 'disabled'}]}
        )
        assert r.status_code >= 400 and len(await events()) == n
        await call('PUT', f'/api/v1/model-bindings/{b}', {**bb, 'enabled': True})
        await call('GET', '/api/models')
        report['M07'] = {'disabled': b, 'unaffected': a, 'dispatch_blocked': r.status_code}
        save()
        audio = await call('GET', '/api/v1/audio/config')
        audio['tts'].update(ENGINE='connection', MODEL=ac, VOICE='synthetic')
        audio['stt'].update(ENGINE='connection', MODEL=bc)
        await call('POST', '/api/v1/audio/config/update', audio)
        payload = {
            'input': 'Synthetic cache ' + uuid.uuid4().hex,
            'voice': 'synthetic',
            '_oi_context': {'chat_id': cid, 'message_id': 'speech'},
        }
        n = len(await events())
        for _ in range(2):
            r = await c.post('/api/v1/audio/speech', json=payload)
            assert r.status_code == 200 and r.content[:4] == b'RIFF'
        assert len(await events()) == n + 1
        audio['tts']['MODEL'] = bc
        await call('POST', '/api/v1/audio/config/update', audio)
        r = await c.post('/api/v1/audio/speech', json=payload)
        assert r.status_code == 200 and len(await events()) == n + 2
        source = io.BytesIO()
        with wave.open(source, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\0\0' * 2400)
        r = await c.post(
            '/api/v1/audio/transcriptions', files={'file': ('synthetic.wav', source.getvalue(), 'audio/wav')}
        )
        assert r.status_code == 200 and 'Synthetic alpha' in r.text
        report['M17'] = {'cache_same_binding_once': True, 'same_text_other_binding_new_call': True, 'stt': bc}
        save()
        images = await call('GET', '/api/v1/images/config')
        images.update(
            ENABLE_IMAGE_GENERATION=True,
            ENABLE_IMAGE_EDIT=True,
            IMAGE_GENERATION_ENGINE='connection',
            IMAGE_EDIT_ENGINE='connection',
            IMAGE_GENERATION_MODEL=ac,
            IMAGE_EDIT_MODEL=bc,
            IMAGE_SIZE='',
            IMAGE_EDIT_SIZE='',
        )
        await call('POST', '/api/v1/images/config/update', images)
        generated = await call('POST', '/api/v1/images/generations', {'prompt': 'synthetic image', 'n': 1})
        png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y9bUAAAAASUVORK5CYII='
        edited = await call(
            'POST', '/api/v1/images/edit', {'prompt': 'synthetic edit', 'image': 'data:image/png;base64,' + png}
        )
        assert generated and edited
        ev = await events()
        assert any(x['operation'] == 'images/generations' and x['connection'] == 'a' for x in ev) and any(
            x['operation'] == 'images/edits' and x['connection'] == 'b' for x in ev
        )
        report['M18'] = {
            'generation': ac,
            'edit': bc,
            'reopened': (await call('GET', '/api/v1/images/config'))['IMAGE_EDIT_MODEL'] == bc,
        }
        save()
        # Actual authenticated statistics: copying a conversation never creates another inference.
        before = await call('GET', '/api/v1/analytics/tokens')
        models_before = await call('GET', '/api/v1/analytics/models')
        daily_before = await call('GET', '/api/v1/analytics/daily')
        # Persist a complete visible message tree as the normal client does for non-streaming callers.
        await call(
            'POST',
            f'/api/v1/chats/{cid}',
            {
                'chat': {
                    'models': [b],
                    'history': {
                        'currentId': mid,
                        'messages': {
                            uid: {
                                'id': uid,
                                'role': 'user',
                                'content': 'synthetic',
                                'parentId': None,
                                'childrenIds': [mid],
                                'models': [b],
                            },
                            mid: {
                                'id': mid,
                                'role': 'assistant',
                                'model': b,
                                'modelName': 'Same chat',
                                'content': 'Synthetic b / same-model reply.',
                                'parentId': uid,
                                'childrenIds': [],
                                'done': True,
                            },
                        },
                    },
                }
            },
        )
        fork = await call('POST', f'/api/v1/chats/{cid}/fork', {'message_id': mid})
        shared = await call('POST', f'/api/v1/chats/{cid}/share')
        shared_view = await call('GET', f'/api/v1/chats/share/{shared["share_id"]}')
        identity = shared_view['chat']['history']['messages'][mid]['oi_model_identity']
        assert identity['connection']['name'] == 'Stage4 B' and 'credentials' not in str(identity)
        assert shared
        data = (await c.get(f'/api/v1/chats/{cid}/archive-bundle')).content
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifest = json.loads(z.read('manifest.json'))
            assert any(x['id'] == b and x['connection']['name'] == 'Stage4 B' for x in manifest['model_references']), (
                manifest['model_references']
            )
        restored = []
        for _ in range(2):
            r = await c.post(
                '/api/v1/archives/import',
                files={'file': ('synthetic.zip', data, 'application/zip')},
                data={'import_id': str(uuid.uuid4()), 'model_mapping': '{}'},
            )
            assert r.status_code == 200, r.text[:250]
            restored.append(r.json()['id'])
        after = await call('GET', '/api/v1/analytics/tokens')
        assert before == after, (before, after)
        assert models_before == await call('GET', '/api/v1/analytics/models')
        assert daily_before == await call('GET', '/api/v1/analytics/daily')
        history = await call('GET', '/api/v1/model-bindings/history')
        assert any(x['id'] == b and x['connection']['name'] == 'Stage4 B' for x in history)
        report['M20'] = {
            'before_tokens': before['total_tokens'],
            'after_tokens': after['total_tokens'],
            'model_counts_equal': True,
            'daily_equal': True,
        }
        report['M22'] = {
            'source': cid,
            'fork': fork['id'],
            'imports': restored,
            'share': shared.get('share_id'),
            'history_name_retained': True,
        }
        save()
        # Owner-only vectors cannot be queried by another user; revoked binding never dispatches.
        vector = json.loads((p / 'vector-evidence.json').read_text())
        query = {
            'collection_names': [x['collection_name'] for x in vector['files']],
            'query': 'synthetic alpha',
            'k': 2,
            'hybrid': True,
        }
        n = len(await events())
        r = await c.post(
            '/api/v1/retrieval/query/collection',
            json=query,
            headers={'Authorization': 'Bearer ' + seed['reader']['token']},
        )
        assert r.status_code in [403, 404] and len(await events()) == n, (r.status_code, r.text[:200])
        conn = next(x for x in await call('GET', '/api/v1/connections') if x['id'] == seed['connections']['a']['id'])
        disabled = await call('PUT', f'/api/v1/connections/{conn["id"]}', {**conn, 'enabled': False})
        try:
            r = await c.post('/api/v1/retrieval/query/collection', json=query)
            assert r.status_code >= 400 and len(await events()) == n, (r.status_code, r.text[:200])
        finally:
            await call('PUT', f'/api/v1/connections/{conn["id"]}', {**disabled, 'enabled': True})
        limited = next(
            (
                x
                for x in await call('GET', '/api/v1/model-bindings')
                if x['upstream_id'] == 'limited' and x['connection_id'] == conn['id']
            ),
            None,
        ) or await call(
            'POST',
            '/api/v1/model-bindings',
            {
                'connection_id': conn['id'],
                'upstream_id': 'limited',
                'name': 'Synthetic limited',
                'protocol': 'openai-chat',
                'capabilities': {'embedding': True},
            },
        )
        await call(
            'POST',
            '/api/v1/retrieval/embedding/update',
            {'RAG_EMBEDDING_ENGINE': 'connection', 'RAG_EMBEDDING_MODEL': limited['id'], 'RAG_EMBEDDING_BATCH_SIZE': 4},
        )
        try:
            n = len(await events())
            r = await c.post(
                '/api/v1/retrieval/query/collection', json={**query, 'collection_names': query['collection_names'][:1]}
            )
            assert r.status_code == 429 and len(await events()) == n + 1, (r.status_code, r.text[:200])
        finally:
            await call(
                'POST',
                '/api/v1/retrieval/embedding/update',
                {'RAG_EMBEDDING_ENGINE': 'connection', 'RAG_EMBEDDING_MODEL': ac, 'RAG_EMBEDDING_BATCH_SIZE': 4},
            )
        report['M19'] = {
            'private_collection_denied': True,
            'revoked_binding_denied': True,
            'rate_limit': 429,
            'rate_limit_calls': 1,
        }
        save()
        print('Remaining HTTP matrix passed', list(report))


asyncio.run(main())
