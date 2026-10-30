"""Empty native terminal results and Gemini media across real HTTP/Socket.IO."""

import asyncio, json, os, uuid
from pathlib import Path
import httpx, socketio

ROOT = Path(os.environ['OI_STAGE6_STATE'])
API = os.environ['OI_STAGE6_API']


async def main():
    seed = json.loads((ROOT / 'seed.json').read_text())
    report = []
    async with httpx.AsyncClient(
        base_url=API, trust_env=False, timeout=30, headers={'Authorization': 'Bearer ' + seed['owner']['token']}
    ) as c:
        socket = socketio.AsyncClient(reconnection=False)
        queue = asyncio.Queue()
        received = []

        @socket.on('events')
        async def event(value):
            received.append(value)
            data = value.get('data', {})
            if data.get('type') == 'chat:completion' and data.get('data', {}).get('done'):
                await queue.put(value)

        await socket.connect(
            API, auth={'token': seed['owner']['token']}, socketio_path='ws/socket.io', transports=['websocket']
        )
        try:
            for saved in [False, True]:
                for stream in [False, True]:
                    for case in ['blocked', 'reasoning_only', 'image_only', 'mixed_image']:
                        received.clear()
                        mid, uid, stable = [str(uuid.uuid4()) for _ in range(3)]
                        chat = stable if saved else 'local:' + socket.get_sid()
                        if saved:
                            (await c.post('/api/v1/chats/drafts', json={'chat_id': chat})).raise_for_status()
                        form = {
                            'model': seed['bindings']['gemini']['id'],
                            'stream': stream,
                            'chat_id': chat,
                            'id': mid,
                            'session_id': socket.get_sid(),
                            'oi_request_id': str(uuid.uuid4()),
                            'messages': [{'role': 'user', 'content': 'Synthetic terminal/media acceptance.'}],
                            'user_message': {
                                'id': uid,
                                'role': 'user',
                                'content': 'Synthetic terminal/media acceptance.',
                            },
                            'params': {'provider_options': {'fixture_case': case}},
                            'background_tasks': {},
                        }
                        if not saved:
                            form['oi_temporary_id'] = stable
                        result = await c.post('/api/chat/completions', json=form)
                        result.raise_for_status()
                        final = await asyncio.wait_for(queue.get(), 30)
                        assert final['message_id'] == mid, final
                        data = final['data']['data']
                        expected = (
                            'content_filter' if case == 'blocked' else 'length' if case == 'reasoning_only' else 'stop'
                        )
                        assert data.get('finish_reason') == expected, data
                        assert data.get('usage'), data
                        if case == 'reasoning_only':
                            assert 'Synthetic brief thought.' in json.dumps(data['output'])
                        files = [
                            f
                            for e in received
                            if e.get('message_id') == mid and e['data']['type'] == 'chat:message:files'
                            for f in e['data']['data']['files']
                        ]
                        if 'image' in case:
                            assert len(files) == 1, received
                            image = files[0]['url']
                            if saved:
                                image_response = await c.get(image)
                                image_response.raise_for_status()
                                assert image_response.content.startswith(b'\x89PNG')
                            else:
                                assert image.startswith('data:image/png;base64,')
                        if saved:
                            for _ in range(30):
                                response = await c.get('/api/v1/chats/' + chat)
                                response.raise_for_status()
                                message = response.json()['chat']['history']['messages'].get(mid, {})
                                if message.get('done') and message.get('finish_reason') == expected:
                                    break
                                await asyncio.sleep(0.1)
                            assert message.get('done') and message.get('finish_reason') == expected, message
                            assert message.get('usage'), message
                            if files:
                                assert message.get('files'), message
                        report.append(
                            {
                                'saved': saved,
                                'stream': stream,
                                'case': case,
                                'finish': expected,
                                'usage': True,
                                'image_files': len(files),
                            }
                        )
        finally:
            await socket.disconnect()
    (ROOT / 'round2-terminal-media.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Sixteen saved/temporary stream/nonstream empty-terminal and Gemini media scenarios passed')


asyncio.run(main())
