"""Synthetic HTTP / authenticated sockets / PostgreSQL CAS stage5 acceptance.
Run only against the isolated stage5 app, never a production server.
"""

import asyncio, base64, json, os, time, uuid
from pathlib import Path
from urllib.parse import urlsplit
import httpx, pyotp, socketio, websockets

API = os.environ.get('OI_STAGE5_API', 'http://127.0.0.1:8096')
assert urlsplit(API).hostname in {'127.0.0.1', 'localhost'}
p = Path(os.environ.get('OI_STAGE5_STATE', '.oi-local/stage5'))
p.mkdir(parents=True, exist_ok=True)
password = 'stage5-synthetic-password'
report = {}


def claims(token):
    return json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '=='))


async def main():
    async with httpx.AsyncClient(base_url=API, timeout=45, trust_env=False) as c:

        async def req(method, path, body=None, token=None, expected=200, **kw):
            r = await c.request(
                method, path, json=body, headers={'Authorization': 'Bearer ' + (token or owner['token'])}, **kw
            )
            assert r.status_code == expected, (path, r.status_code, r.text[:250])
            return r

        r = await c.post(
            '/api/v1/auths/signup',
            json={'name': 'Stage5 Owner', 'email': 'oi-stage5-owner@example.invalid', 'password': password},
        )
        if r.status_code != 200:
            r = await c.post(
                '/api/v1/auths/signin', json={'email': 'oi-stage5-owner@example.invalid', 'password': password}
            )
        r.raise_for_status()
        owner = r.json()

        async def config(values):
            return (await req('POST', '/api/v1/configs/import', {'config': values})).json()

        async def login(user, pw=password):
            return (await c.post('/api/v1/auths/signin', json={'email': user['email'], 'password': pw})).json()

        reader = (
            await req(
                'POST',
                '/api/v1/auths/add',
                {
                    'name': 'Stage5 Writer',
                    'email': f'stage5-writer-{uuid.uuid4().hex[:6]}@example.invalid',
                    'password': password,
                    'role': 'user',
                },
            )
        ).json()
        writer = await login(reader)
        conn = (
            await req(
                'POST',
                '/api/v1/connections',
                {
                    'name': 'Stage5 Synthetic',
                    'template': 'openai-compatible',
                    'base_url': os.environ.get('OI_STAGE5_PROVIDER', 'http://127.0.0.1:8097') + '/a/v1',
                    'api_key': 'synthetic-a',
                    'settings': {'cost_currency': 'USD'},
                    'shared': True,
                },
            )
        ).json()
        model = (
            await req(
                'POST',
                '/api/v1/model-bindings',
                {
                    'connection_id': conn['id'],
                    'upstream_id': 'same-model',
                    'name': 'Stage5 Chat',
                    'protocol': 'openai-chat',
                    'capabilities': {'chat': True, 'tools': True},
                },
            )
        ).json()
        voice = (
            await req(
                'POST',
                '/api/v1/model-bindings',
                {
                    'connection_id': conn['id'],
                    'upstream_id': 'cap-model',
                    'name': 'Stage5 Voice',
                    'protocol': 'openai-chat',
                    'capabilities': {'realtime': True, 'stt': True},
                },
            )
        ).json()
        await config(
            {
                'task.title.enable': False,
                'task.tags.enable': False,
                'task.follow_up.enable': False,
                'ui.default_models': model['id'],
            }
        )
        # Actual expiry, HTTP renewal and live Socket.IO credential replacement.
        await config({'auth.jwt_expiry': '4s'})
        old = await login(reader)
        old_claims = claims(old['token'])
        sio = socketio.AsyncClient(reconnection=False)
        await sio.connect(API, auth={'token': old['token']}, socketio_path='ws/socket.io', transports=['websocket'])
        sid = sio.get_sid()
        await config({'auth.jwt_expiry': '1h'})
        renewed = await req('POST', '/api/v1/auths/refresh', token=old['token'])
        fresh = renewed.json()
        fresh_claims = claims(fresh['token'])
        assert (
            fresh_claims['auth_time'] == old_claims['auth_time']
            and fresh_claims['session_stamp'] == old_claims['session_stamp']
            and fresh_claims['jti'] != old_claims['jti']
        )
        assert (
            'HttpOnly' in renewed.headers['set-cookie']
            and 'SameSite=' in renewed.headers['set-cookie']
            and renewed.headers.get('cache-control') == 'no-store'
        )
        await sio.call('user-join', {'auth': {'token': fresh['token']}}, timeout=5)
        await asyncio.sleep(max(0, old_claims['exp'] - time.time() + 0.3))
        await req('POST', '/api/v1/auths/refresh', token=old['token'], expected=401)
        await sio.call('heartbeat', {}, timeout=5)
        assert sio.connected and sio.get_sid() == sid
        report['A01'] = {
            'auth_time_preserved': True,
            'cookie_http_only_same_site': True,
            'no_store': True,
            'expired_token_rejected': True,
            'same_live_socket_after_old_expiry': True,
        }
        # Account switching on a live socket cannot retain the previous account's rooms.
        await sio.emit('user-join', {'auth': {'token': owner['token']}})
        for _ in range(30):
            if not sio.connected:
                break
            await asyncio.sleep(0.1)
        assert not sio.connected
        report['A02_socket_switch'] = 'old_socket_disconnected'
        # Real native voice refresh without forwarding the token/control event to the provider.
        audio = (await req('GET', '/api/v1/audio/config')).json()
        audio['realtime'].update(
            ENABLED=True, PROVIDER='connection', MODEL=voice['id'], TRANSCRIPTION_MODEL=voice['id'], VOICE='synthetic'
        )
        await req('POST', '/api/v1/audio/config/update', audio)
        old_voice = await login(reader)
        async with websockets.connect(API.replace('http:', 'ws:') + '/api/v1/audio/realtime') as ws:
            await ws.send(json.dumps({'type': 'auth', 'token': old_voice['token'], 'model_id': model['id']}))
            ready = json.loads(await asyncio.wait_for(ws.recv(), 5))
            assert ready['type'] == 'bridge.ready', ready
            renewed_voice = (await req('POST', '/api/v1/auths/refresh', token=old_voice['token'])).json()
            await ws.send(json.dumps({'type': 'bridge.auth.refresh', 'token': renewed_voice['token']}))
            assert json.loads(await asyncio.wait_for(ws.recv(), 5))['type'] == 'bridge.auth.refreshed'
            await ws.send(json.dumps({'type': 'bridge.ping'}))
            assert json.loads(await asyncio.wait_for(ws.recv(), 5))['type'] == 'bridge.pong'
            await ws.send(json.dumps({'type': 'bridge.auth.refresh', 'token': owner['token']}))
            event = json.loads(await asyncio.wait_for(ws.recv(), 5))
            assert event['type'] == 'bridge.error'
        report['A01_voice'] = {'same_session_renewal': True, 'other_account_rejected': True}
        # PostgreSQL HTTP concurrency, long draft and versioned acknowledgements.
        input_version = str(uuid.uuid4())
        new_version = str(uuid.uuid4())
        cid = str(uuid.uuid4())
        await req('POST', '/api/v1/chats/drafts', {'chat_id': cid}, token=fresh['token'])

        async def put(i):
            return await c.put(
                f'/api/v1/chats/{cid}/draft',
                headers={'Authorization': 'Bearer ' + fresh['token']},
                json={
                    'revision': 0,
                    'mutation_id': str(uuid.uuid4()),
                    'payload': {'prompt': '中文长草稿' * 2000, 'writer': i, '_oi_input_version': input_version},
                },
            )

        writes = await asyncio.gather(*(put(i) for i in range(6)))
        assert sorted(r.status_code for r in writes) == [200, 409, 409, 409, 409, 409]
        saved = (await req('GET', f'/api/v1/chats/{cid}/draft', token=fresh['token'])).json()
        assert len(saved['payload']['prompt']) > 5000
        await req(
            'PUT',
            f'/api/v1/chats/{cid}/draft',
            {
                'revision': 1,
                'mutation_id': str(uuid.uuid4()),
                'payload': {'prompt': 'new typing', '_oi_input_version': new_version},
            },
            token=fresh['token'],
        )
        ack = (
            await req(
                'POST',
                f'/api/v1/chats/{cid}/draft/acknowledge',
                {'revision': 1, 'mutation_id': str(uuid.uuid4()), 'input_version': input_version},
                token=fresh['token'],
            )
        ).json()
        assert ack['payload']['prompt'] == 'new typing' and ack['applied'] is False
        outsider = (
            await req(
                'POST',
                '/api/v1/auths/add',
                {
                    'name': 'Stage5 Other',
                    'email': f'stage5-other-{uuid.uuid4().hex[:6]}@example.invalid',
                    'password': password,
                    'role': 'user',
                },
            )
        ).json()
        outsider = await login(outsider)
        denied = await c.get(f'/api/v1/chats/{cid}/draft', headers={'Authorization': 'Bearer ' + outsider['token']})
        assert denied.status_code in [401, 403, 404]
        denied = await c.put(
            f'/api/v1/chats/{cid}/draft',
            headers={'Authorization': 'Bearer ' + outsider['token']},
            json={'revision': 2, 'mutation_id': str(uuid.uuid4()), 'payload': {'prompt': 'forbidden'}},
        )
        assert denied.status_code in [401, 403, 404]
        report['D01_D06_D09_D10'] = {
            'chat': cid,
            'concurrent_winners': 1,
            'conflicts': 5,
            'characters': len(saved['payload']['prompt']),
            'new_input_survived_ack': True,
            'other_account_denied': True,
        }
        # Password, persistent account revocation and Redis per-token logout.
        await req(
            'POST',
            '/api/v1/auths/update/password',
            {'password': password, 'new_password': password + '-changed'},
            token=fresh['token'],
        )
        await req('POST', '/api/v1/auths/refresh', token=fresh['token'], expected=401)
        writer = await login(reader, password + '-changed')
        await req('POST', f'/api/v1/users/{reader["id"]}/sessions/revoke')
        await req('POST', '/api/v1/auths/refresh', token=writer['token'], expected=401)
        writer = await login(reader, password + '-changed')
        await req('POST', '/api/v1/auths/signout', token=writer['token'])
        await req('POST', '/api/v1/auths/refresh', token=writer['token'], expected=401)
        report['A04_revocation'] = {'password_change': 401, 'admin_revoke': 401, 'redis_token_logout': 401}
        # MFA policy and factor replacement through real human-authenticated endpoints.
        settings = (await req('GET', '/api/v1/auths/admin/config')).json()
        settings['ENABLE_MFA'] = True
        await req('POST', '/api/v1/auths/admin/config', settings)
        await req('POST', '/api/v1/auths/refresh', token=owner['token'], expected=401)
        challenge = await login(owner)
        assert challenge.get('challenge_token')
        setup = (
            await req('POST', '/api/v1/auths/mfa/enroll/start', {'challenge_token': challenge['challenge_token']})
        ).json()
        enrolled = (
            await req(
                'POST',
                '/api/v1/auths/mfa/enroll/confirm',
                {'challenge_token': challenge['challenge_token'], 'code': pyotp.TOTP(setup['manual_key']).now()},
            )
        ).json()
        owner = enrolled
        refreshed = (await req('POST', '/api/v1/auths/refresh')).json()
        assert (
            claims(refreshed['token'])['mfa_verified'] is True
            and claims(refreshed['token'])['auth_time'] == claims(owner['token'])['auth_time']
        )
        replacement = (
            await req('POST', '/api/v1/auths/mfa/replace', {'code': enrolled['recovery_codes'][0], 'recovery': True})
        ).json()
        setup = (
            await req('POST', '/api/v1/auths/mfa/enroll/start', {'challenge_token': replacement['challenge_token']})
        ).json()
        replaced = (
            await req(
                'POST',
                '/api/v1/auths/mfa/enroll/confirm',
                {'challenge_token': replacement['challenge_token'], 'code': pyotp.TOTP(setup['manual_key']).now()},
            )
        ).json()
        await req('POST', '/api/v1/auths/refresh', token=refreshed['token'], expected=401)
        challenge = await login(owner)
        owner = (
            await req(
                'POST',
                '/api/v1/auths/mfa/verify',
                {
                    'challenge_token': challenge['challenge_token'],
                    'code': replaced['recovery_codes'][0],
                    'recovery': True,
                },
            )
        ).json()
        settings = (await req('GET', '/api/v1/auths/admin/config')).json()
        settings['ENABLE_MFA'] = False
        await req('POST', '/api/v1/auths/admin/config', settings)
        owner = await login(owner)
        await config({'auth.jwt_expiry': '1h'})
        writer = await login(reader, password + '-changed')
        report['A04_mfa'] = {
            'policy_revokes_previous_sessions': True,
            'renewal_keeps_mfa_and_auth_time': True,
            'factor_replacement_revokes_previous_session': True,
        }
        seed = {
            'owner': owner,
            'writer': writer,
            'other': outsider,
            'model': model,
            'voice': voice,
            'connection': conn,
            'password': password,
            'writer_password': password + '-changed',
        }
        (p / 'seed.json').write_text(json.dumps(seed))
        (p / 'seed.json').chmod(0o600)
        (p / 'http-evidence.json').write_text(json.dumps(report, indent=2))
        print('Stage5 real HTTP/Socket/PostgreSQL/MFA acceptance passed:', list(report))


asyncio.run(main())
