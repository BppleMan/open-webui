"""Synthetic stage4 integration scenario; run with scripts/oi/stage4-acceptance.py."""

import os
import asyncio, base64, json, pathlib, sqlite3, uuid
import httpx, websockets

p = pathlib.Path(os.environ['OI_STAGE4_STATE'])
seed = json.loads((p / 'seed.json').read_text())
report = []


async def main():
    async with httpx.AsyncClient(
        base_url=os.environ.get('OI_STAGE4_API', 'http://127.0.0.1:8094'),
        headers={'Authorization': 'Bearer ' + seed['owner']['token']},
        timeout=30,
    ) as c:

        async def call(method, path, body=None):
            r = await c.request(method, path, json=body)
            assert r.is_success, (path, r.status_code, r.text[:200])
            return r.json()

        cfg = await call('GET', '/api/v1/audio/config')
        cfg['realtime'].update(
            ENABLED=True,
            PROVIDER='connection',
            MODEL=seed['bindings']['a_cap']['id'],
            TRANSCRIPTION_MODEL=seed['bindings']['a_cap']['id'],
            VOICE='synthetic',
        )
        await call('POST', '/api/v1/audio/config/update', cfg)
        for case in [
            'normal',
            'disconnect',
            'connection_disabled',
            'credentials_rotated',
            'session_revoked',
            'idle_disabled',
        ]:
            await c.post(
                os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095') + '/mode',
                json={'mode': 'normal' if case == 'normal' else 'disconnect' if case == 'disconnect' else 'hold'},
            )
            cid = str(uuid.uuid4())
            await call('POST', '/api/v1/chats/drafts', {'chat_id': cid})
            connection = next(
                x for x in await call('GET', '/api/v1/connections') if x['id'] == seed['connections']['a']['id']
            )
            old_stamp = None
            try:
                async with websockets.connect(
                    os.environ.get('OI_STAGE4_API', 'http://127.0.0.1:8094').replace('http:', 'ws:')
                    + '/api/v1/audio/realtime'
                ) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                'type': 'auth',
                                'token': seed['owner']['token'],
                                'chat_id': cid,
                                'model_id': seed['bindings']['a_chat']['id'],
                            }
                        )
                    )
                    ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    assert ready['type'] == 'bridge.ready', ready
                    await ws.send(
                        json.dumps(
                            {'type': 'input_audio_buffer.append', 'audio': base64.b64encode(b'\0\0' * 50).decode()}
                        )
                    )
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        if event['type'] == 'conversation.item.input_audio_transcription.completed':
                            break
                    await ws.send(json.dumps({'type': 'bridge.respond', 'item_id': event['item_id']}))
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        if event['type'] == ('response.done' if case == 'normal' else 'response.created'):
                            break
                    before = len(
                        (await c.get(os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095') + '/events')).json()
                    )
                    if case in ['connection_disabled', 'idle_disabled']:
                        connection = await call(
                            'PUT', f'/api/v1/connections/{connection["id"]}', {**connection, 'enabled': False}
                        )
                    elif case == 'credentials_rotated':
                        connection = await call(
                            'PUT',
                            f'/api/v1/connections/{connection["id"]}',
                            {**connection, 'api_key': 'synthetic-rotated'},
                        )
                    elif case == 'session_revoked':
                        with sqlite3.connect(p / 'data/webui.db') as db:
                            old_stamp = db.execute(
                                'select session_stamp from auth where id=?', (seed['owner']['id'],)
                            ).fetchone()[0]
                            db.execute(
                                'update auth set session_stamp=? where id=?', (str(uuid.uuid4()), seed['owner']['id'])
                            )
                    if case not in ['normal', 'disconnect', 'idle_disabled']:
                        await ws.send(
                            json.dumps(
                                {'type': 'input_audio_buffer.append', 'audio': base64.b64encode(b'\0\0' * 50).decode()}
                            )
                        )
                    if case != 'normal':
                        try:
                            while True:
                                error = json.loads(await asyncio.wait_for(ws.recv(), 12))
                                if error.get('type') == 'bridge.error':
                                    break
                        except websockets.ConnectionClosed:
                            pass
                        except asyncio.TimeoutError:
                            raise AssertionError('Revoked/interrupted socket was not closed')
                        if case not in ['disconnect']:
                            assert (
                                len(
                                    (
                                        await c.get(
                                            os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095') + '/events'
                                        )
                                    ).json()
                                )
                                == before
                            ), case
            finally:
                if old_stamp is not None:
                    with sqlite3.connect(p / 'data/webui.db') as db:
                        db.execute('update auth set session_stamp=? where id=?', (old_stamp, seed['owner']['id']))
                if case in ['connection_disabled', 'idle_disabled', 'credentials_rotated']:
                    await call(
                        'PUT',
                        f'/api/v1/connections/{connection["id"]}',
                        {**connection, 'enabled': True, 'api_key': 'synthetic-a'},
                    )
            usage = None
            for _ in range(30):
                usage = await call('GET', f'/api/v1/chats/{cid}/usage')
                if len(usage['calls']) == 2 and all(x['status'] not in ['pending', 'running'] for x in usage['calls']):
                    break
                await asyncio.sleep(0.1)
            assert len(usage['calls']) == 2, usage
            voice = next(x for x in usage['calls'] if x['usage'].get('context', {}).get('purpose') == 'realtime')
            assert voice['usage']['input_tokens'] == 123, voice
            assert voice['status'] == ('completed' if case == 'normal' else 'interrupted'), voice
            if case != 'normal':
                assert voice['usage'].get('output_tokens') is None and voice['actual_usd'] is None
            report.append(
                {
                    'case': case,
                    'chat_id': cid,
                    'call_count': 2,
                    'voice_usage': voice['usage'],
                    'status': voice['status'],
                }
            )
            (p / 'realtime-evidence.json').write_text(json.dumps(report, indent=2))
            print(case, 'passed')
        await c.post(os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095') + '/mode', json={'mode': 'normal'})


asyncio.run(main())
