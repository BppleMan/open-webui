"""Real shared-write ACL, private native projection and temporary metering acceptance."""

import asyncio, json, os, uuid
from pathlib import Path
import httpx, socketio
from sqlalchemy import select
from open_webui.internal.db import get_async_db
from open_webui.models.users import Users
from open_webui.models.customization import ChatLedger, Generation, ModelCall

root = Path(os.environ['OI_STAGE6_STATE'])
api = os.environ['OI_STAGE6_API']
secret = 'synthetic-owner-only-instructions'


async def main():
    seed = json.loads((root / 'seed.json').read_text())
    owner = seed['owner']
    async with httpx.AsyncClient(base_url=api, trust_env=False, timeout=30) as client:

        async def req(user, method, path, body=None):
            headers = {'Authorization': 'Bearer ' + user['token']} if user else {}
            if user is None:
                async with httpx.AsyncClient(base_url=api, trust_env=False, timeout=30) as anonymous:
                    r = await anonymous.request(method, path, json=body)
            else:
                r = await client.request(method, path, json=body, headers=headers)
            assert r.is_success, (path, r.status_code, r.text[:500])
            r.raise_for_status()
            return r.json()

        actors = {}
        for name in ['writer', 'reader']:
            login = {'email': f'round3-{name}@example.invalid', 'password': 'synthetic-round3-password'}
            u = await req(owner, 'POST', '/api/v1/auths/add', {'name': name, 'role': 'user', **login})
            async with get_async_db() as db:
                await Users.update_user_by_id(u['id'], {'role': 'user'}, db=db)
            actors[name] = await req(None, 'POST', '/api/v1/auths/signin', login)
        writer, reader = actors['writer'], actors['reader']
        cid = str(uuid.uuid4())
        model = seed['bindings']['openai-responses']['id']
        created = await req(
            owner,
            'POST',
            '/api/v1/chats/new',
            {
                'chat': {
                    'id': cid,
                    'title': 'Round3 synthetic shared conversation',
                    'models': [model],
                    'history': {'currentId': None, 'messages': {}},
                }
            },
        )

        cid = created['id']

        async def ask(user, temporary=False, model_id=model):
            sock = socketio.AsyncClient(reconnection=False)
            events = []
            done = asyncio.Event()
            mid, uid = [str(uuid.uuid4()) for _ in range(2)]

            @sock.on('events')
            async def event(value):
                if value.get('message_id') != mid:
                    return
                events.append(value)
                if value.get('data', {}).get('data', {}).get('done'):
                    done.set()

            await sock.connect(
                api, auth={'token': user['token']}, socketio_path='ws/socket.io', transports=['websocket']
            )
            stable = str(uuid.uuid4())
            body = {
                'chat_id': 'local:' + sock.get_sid() if temporary else cid,
                'id': mid,
                'session_id': sock.get_sid(),
                'model': model_id,
                'stream': True,
                'oi_request_id': str(uuid.uuid4()),
                'messages': [{'role': 'user', 'content': 'Synthetic shared write.'}],
                'user_message': {'id': uid, 'role': 'user', 'content': 'Synthetic shared write.'},
                'params': {'provider_options': {'instructions': secret}} if model_id == model else {},
                'background_tasks': {},
            }
            if temporary:
                body['oi_temporary_id'] = stable
            try:
                await req(user, 'POST', '/api/chat/completions', body)
                await asyncio.wait_for(done.wait(), 25)
                assert not any(e.get('data', {}).get('type') == 'chat:message:error' for e in events), events
                return mid, stable
            finally:
                await sock.disconnect()

        first, _ = await ask(owner)
        shared = await req(owner, 'POST', f'/api/v1/chats/{cid}/share', {'share_mode': 'continue'})
        grants = [
            {'principal_type': 'user', 'principal_id': writer['id'], 'permission': 'write'},
            {'principal_type': 'user', 'principal_id': writer['id'], 'permission': 'read'},
            {'principal_type': 'user', 'principal_id': reader['id'], 'permission': 'read'},
            {'principal_type': 'anyone', 'principal_id': '*', 'permission': 'read'},
        ]
        await req(
            owner,
            'POST',
            f'/api/v1/chats/shared/{cid}/access/update',
            {'access_grants': grants, 'share_mode': 'continue'},
        )
        second, _ = await ask(writer)
        await ask(owner)
        for actor in [writer, reader, None]:
            view = await req(actor, 'GET', '/api/v1/chats/share/' + shared['share_id'])
            assert secret not in json.dumps(view) and 'oi_native' not in json.dumps(view)
        own = await req(owner, 'GET', '/api/v1/chats/' + cid)
        assert secret in json.dumps(own), 'Owner native state must remain available'
        # In upstream continue mode, a readable share is collaborative. A snapshot
        # share is the read-only case; preserve that contract rather than redefining ACLs.
        await req(
            owner, 'POST', f'/api/v1/chats/shared/{cid}/access/update', {'access_grants': grants, 'share_mode': None}
        )
        denied = await client.post(
            '/api/chat/completions',
            headers={'Authorization': 'Bearer ' + reader['token']},
            json={
                'chat_id': cid,
                'model': model,
                'id': str(uuid.uuid4()),
                'messages': [{'role': 'user', 'content': 'Must not dispatch'}],
            },
        )
        assert denied.status_code in (401, 403, 404), denied.text
        await req(
            owner, 'POST', f'/api/v1/chats/shared/{cid}/access/update', {'access_grants': [], 'share_mode': 'continue'}
        )
        denied = await client.post(
            '/api/chat/completions',
            headers={'Authorization': 'Bearer ' + writer['token']},
            json={
                'chat_id': cid,
                'model': model,
                'id': str(uuid.uuid4()),
                'messages': [{'role': 'user', 'content': 'Revoked must not dispatch'}],
            },
        )
        assert denied.status_code in (401, 403, 404), denied.text
        async with get_async_db() as db:
            assert (await db.get(ChatLedger, cid)).user_id == owner['id']
            actors_in_db = list(
                (await db.execute(select(Generation.user_id).where(Generation.chat_id == cid))).scalars()
            )
            assert actors_in_db.count(owner['id']) == 2 and actors_in_db.count(writer['id']) == 1, actors_in_db
        _, temporary = await ask(owner, True, seed['bindings']['ollama']['id'])
        async with get_async_db() as db:
            calls = list(
                (await db.execute(select(ModelCall).where(ModelCall.chat_id == 'temporary:' + temporary))).scalars()
            )
            assert len(calls) == 1
            usage = json.dumps(calls[0].usage)
            assert (
                'synthetic thought' not in usage
                and 'Native stage6 fixture' not in usage
                and 'message' not in calls[0].usage['raw']
            ), usage
        report = {
            'shared_write': 'owner/writer/owner passed',
            'call_actor_preserved': True,
            'ledger_owned_by_chat_owner': True,
            'reader_and_revoked_writer_denied': True,
            'private_native_state_hidden': ['writer', 'reader', 'anonymous'],
            'owner_native_retained': True,
            'temporary_ollama_metering_only': True,
        }
        (root / 'round3-http.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Round3 shared ACL, private projection and Ollama temporary ledger HTTP passed')


asyncio.run(main())
