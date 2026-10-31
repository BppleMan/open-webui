"""Real shared reads, private caller isolation and pure-image fork follow-ups."""

import asyncio, json, os, uuid
from pathlib import Path
import httpx, socketio

root = Path(os.environ['OI_STAGE6_STATE'])
api = os.environ['OI_STAGE6_API']
provider = os.environ['OI_STAGE6_PROVIDER']


async def main():
    seed = json.loads((root / 'seed.json').read_text())
    owner = seed['owner']
    async with httpx.AsyncClient(base_url=api, trust_env=False, timeout=30) as c:

        async def req(actor, method, path, body=None):
            r = await c.request(method, path, headers={'Authorization': 'Bearer ' + actor['token']}, json=body)
            assert r.is_success, (path, r.status_code, r.text[:500])
            return r.json()

        login = {'email': 'round4-writer@example.invalid', 'password': 'round4-synthetic-password'}
        await req(owner, 'POST', '/api/v1/auths/add', {'name': 'Round4 writer', 'role': 'user', **login})
        writer = await req(owner, 'POST', '/api/v1/auths/signin', login)

        async def chat(model):
            return (
                await req(
                    owner,
                    'POST',
                    '/api/v1/chats/new',
                    {
                        'chat': {
                            'models': [model],
                            'title': 'Round4 synthetic boundary',
                            'history': {'currentId': None, 'messages': {}},
                        }
                    },
                )
            )['id']

        async def share(cid):
            shared = await req(owner, 'POST', f'/api/v1/chats/{cid}/share', {'share_mode': 'continue'})
            await req(
                owner,
                'POST',
                f'/api/v1/chats/shared/{cid}/access/update',
                {
                    'share_mode': 'continue',
                    'access_grants': [
                        {'principal_type': 'user', 'principal_id': writer['id'], 'permission': p}
                        for p in ['read', 'write']
                    ],
                },
            )

            return shared['share_id']

        async def ask(actor, cid, model, parent=None, options=None, stream=False):
            sock = socketio.AsyncClient(reconnection=False)
            mid, uid = [str(uuid.uuid4()) for _ in range(2)]
            done = asyncio.Event()
            events = []

            @sock.on('events')
            async def event(value):
                if value.get('message_id') == mid:
                    events.append(value)
                    if value.get('data', {}).get('data', {}).get('done'):
                        done.set()

            await sock.connect(
                api, auth={'token': actor['token']}, socketio_path='ws/socket.io', transports=['websocket']
            )
            try:
                await req(
                    actor,
                    'POST',
                    '/api/chat/completions',
                    {
                        'chat_id': cid,
                        'id': mid,
                        'parent_id': parent,
                        'session_id': sock.get_sid(),
                        'model': model,
                        'stream': stream,
                        'oi_request_id': str(uuid.uuid4()),
                        'background_tasks': {},
                        'messages': [{'role': 'user', 'content': 'Synthetic follow-up: describe the prior output.'}],
                        'user_message': {
                            'id': uid,
                            'parentId': parent,
                            'role': 'user',
                            'content': 'Synthetic follow-up: describe the prior output.',
                            'models': [model],
                        },
                        'params': {'provider_options': options or {}},
                    },
                )
                await asyncio.wait_for(done.wait(), 25)
                assert not any(e.get('data', {}).get('type') == 'chat:message:error' for e in events), events
                return mid
            finally:
                await sock.disconnect()

        con = await req(
            writer,
            'POST',
            '/api/v1/connections',
            {
                'name': 'Writer private',
                'template': 'openai',
                'protocol': 'openai-responses',
                'base_url': provider + '/openai-responses',
                'settings': {'auth': 'none'},
                'shared': False,
            },
        )
        binding = await req(
            writer,
            'POST',
            '/api/v1/model-bindings',
            {
                'connection_id': con['id'],
                'upstream_id': 'fixture',
                'name': 'Writer private',
                'capabilities': {'chat': True},
                'params': {'provider_options': {'instructions': 'round4-writer-private'}},
            },
        )
        cid = await chat(seed['bindings']['openai-responses']['id'])
        await share(cid)
        await ask(writer, cid, binding['id'], options={'instructions': 'round4-writer-private'})
        owner_view = await req(owner, 'GET', '/api/v1/chats/' + cid)
        writer_view = await req(writer, 'GET', '/api/v1/chats/' + cid)
        assert 'round4-writer-private' not in json.dumps(owner_view)
        assert 'round4-writer-private' in json.dumps(writer_view)
        exported = await c.get('/api/v1/chats/all', headers={'Authorization': 'Bearer ' + owner['token']})
        assert exported.is_success and 'round4-writer-private' not in exported.text
        for stream in [False, True]:
            gemini = seed['bindings']['gemini']['id']
            cid = await chat(gemini)
            mid = await ask(owner, cid, gemini, options={'fixture_case': 'image_only'}, stream=stream)
            share_id = await share(cid)
            clone = await req(writer, 'POST', f'/api/v1/chats/{share_id}/clone/shared')
            assert 'opaque-image' in json.dumps(clone)
            fork = await req(writer, 'POST', f'/api/v1/chats/{cid}/fork', {'message_id': mid})
            assert 'opaque-image' in json.dumps(fork), fork
            await ask(writer, fork['id'], gemini, parent=mid, stream=stream)
            event = (await c.get(provider + '/events')).json()[-1]
            history = json.dumps(event['body']['contents'])
            assert 'inlineData' in history and 'opaque-image' in history, history
    (root / 'round4-http.json').write_text(
        json.dumps(
            {
                'caller_private_instructions': 'passed',
                'owner_json_export_privacy': 'passed',
                'nonowner_pure_image_fork_next_turn_stream_and_nonstream': 'passed',
                'supplier': 'loopback synthetic only',
            },
            indent=2,
        )
        + '\n'
    )
    print('Round4 HTTP private caller projection and pure-image fork replay passed')


asyncio.run(main())
