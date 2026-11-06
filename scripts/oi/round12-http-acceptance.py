"""Real HTTP/socket/DB checks for composed tool identity and Responses endings."""

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import socketio

ROOT = Path(os.environ['OI_STAGE6_STATE'])
API = os.environ['OI_STAGE6_API']
PROVIDER = os.environ['OI_STAGE6_PROVIDER']


async def main():
    seed = json.loads((ROOT / 'seed.json').read_text())
    async with httpx.AsyncClient(
        base_url=API, trust_env=False, timeout=30, headers={'Authorization': 'Bearer ' + seed['owner']['token']}
    ) as c:

        async def req(method, path, body=None):
            r = await c.request(method, path, json=body)
            if r.is_error:
                raise AssertionError(f'{method} {path}: {r.status_code} {r.text}')
            return r.json()

        fid = 'round12_id_fixture'
        code = """class Filter:
    toggle = True
    async def stream(self, event):
        def rewrite(value):
            if isinstance(value, list): return [rewrite(v) for v in value]
            if not isinstance(value, dict): return value
            return {k: 'filtered-'+v if k in {'id','call_id'} and v == 'r74-repeat' else rewrite(v) for k,v in value.items()}
        return rewrite(event)
"""
        await req(
            'POST',
            '/api/v1/functions/create',
            {'id': fid, 'name': 'Synthetic R74 ID filter', 'content': code, 'meta': {}},
        )
        await req('POST', f'/api/v1/functions/id/{fid}/toggle')
        await req('POST', f'/api/v1/functions/id/{fid}/toggle/global')
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
                    data = (event.get('data') or {}).get('data') or {}
                    if event.get('message_id') == mid and predicate(data):
                        return data

            return await asyncio.wait_for(receive(), 30)

        async def new_chat(model):
            return (
                await req(
                    'POST',
                    '/api/v1/chats/new',
                    {
                        'chat': {
                            'models': [model],
                            'title': 'R74 HTTP synthetic',
                            'history': {'currentId': None, 'messages': {}},
                        }
                    },
                )
            )['id']

        async def ask(cid, model, text, parent=None, filtered=False, streaming=True):
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
                    'model': model,
                    'stream': streaming,
                    'chat_id': cid,
                    'id': mid,
                    'parent_id': parent,
                    'session_id': sock.get_sid(),
                    'oi_request_id': str(uuid.uuid4()),
                    'messages': [{'role': 'user', 'content': text}],
                    'user_message': user,
                    'background_tasks': {},
                    'filter_ids': [fid] if filtered else [],
                },
            )
            return mid

        report = []
        try:
            for protocol in ['gemini', 'openai-responses']:
                for filtered in [False, True]:
                    model = seed['bindings'][protocol]['id']
                    cid = await new_chat(model)
                    mid = await ask(cid, model, 'R74_TOOL_CYCLE', filtered=filtered)
                    resolved = set()
                    for turn in range(2):
                        data = await wait(
                            mid,
                            lambda data: any(
                                item.get('name') == 'ask_user'
                                and item.get('status') == 'pending'
                                and item.get('call_id') not in resolved
                                for item in data.get('output', [])
                            ),
                        )
                        call = next(
                            item
                            for item in data['output']
                            if item.get('name') == 'ask_user'
                            and item.get('status') == 'pending'
                            and item.get('call_id') not in resolved
                        )
                        resolved.add(call['call_id'])
                        await req(
                            'POST',
                            f'/api/v1/chats/{cid}/messages/{mid}/resolve',
                            {'call_id': call['call_id'], 'action': 'answer', 'answers': {'route': 'North'}},
                        )
                    await wait(mid, lambda data: data.get('done'))
                    duplicate = await c.post(
                        f'/api/v1/chats/{cid}/messages/{mid}/resolve',
                        json={'call_id': call['call_id'], 'action': 'answer', 'answers': {'route': 'North'}},
                    )
                    assert duplicate.status_code == 409
                    message = (await req('GET', '/api/v1/chats/' + cid))['chat']['history']['messages'][mid]
                    assert len([item for item in message['output'] if item.get('type') == 'function_call_output']) == 2
                    assert len(message['oi_native']) == 3
                    if filtered:
                        assert all(n.get('consumer_invalidated') for n in message['oi_native'][:2])
                    follow = await ask(cid, model, 'R74_FOLLOWUP', parent=mid)
                    await wait(follow, lambda data: data.get('done'))
                    outbound = (await c.get(PROVIDER + '/events')).json()[-1]['body']
                    assert len((await req('GET', f'/api/v1/chats/{cid}/usage'))['calls']) == 4
                    text = json.dumps(outbound)
                    result_key = 'functionResponse' if protocol == 'gemini' else 'function_call_output'
                    assert text.count(result_key) == 2 and text.count('ask_user') >= 2, outbound
                    assert 'r74-terminal-signature' in text and text.index('r74-terminal-signature') > text.rindex(
                        result_key
                    )
                    (ROOT / f'round12-{protocol}-{filtered}.json').write_text(
                        json.dumps({'message': message, 'outbound': outbound}, indent=2) + '\n'
                    )
                    report.append(
                        {
                            'protocol': protocol,
                            'filtered': filtered,
                            'two_resolutions': True,
                            'signed_terminal_after_results': True,
                        }
                    )
            model = seed['bindings']['openai-responses']['id']
            for status in ['incomplete', 'failed']:
                for streaming in [False, True]:
                    cid = await new_chat(model)
                    mid = await ask(cid, model, 'R74_RESPONSE_' + status.upper(), streaming=streaming)
                    await wait(mid, lambda data: data.get('done') and 'output' in data)
                    message = (await req('GET', '/api/v1/chats/' + cid))['chat']['history']['messages'][mid]
                    calls = (await req('GET', f'/api/v1/chats/{cid}/usage'))['calls']
                    assert len(calls) == 1 and calls[0]['status'] == status
                    assert calls[0]['usage']['input_tokens'] == message['usage']['input_tokens'] == 10
                    assert calls[0]['usage']['output_tokens'] == message['usage']['output_tokens'] == 2
                    assert message['response_status'] == status and 'R74 partial answer.' in json.dumps(
                        message['output']
                    )
                    assert message['finish_reason'] == ('length' if status == 'incomplete' else 'error')
                    if status == 'failed':
                        assert message.get('error')
                    else:
                        assert message['incomplete_details'] == {'reason': 'max_output_tokens'}
                    report.append({'status': status, 'streaming': streaming, 'message_and_ledger': 'passed'})
            (ROOT / 'round12-http.json').write_text(json.dumps(report, indent=2) + '\n')
            print(
                'Round12 HTTP: filtered duplicate IDs, signed terminal tool history and Responses message/ledger endings passed'
            )
        finally:
            await sock.disconnect()
            await req('POST', f'/api/v1/functions/id/{fid}/toggle')


asyncio.run(main())
