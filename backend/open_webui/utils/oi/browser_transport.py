"""Run native protocol requests through the owner's existing browser RPC."""

import asyncio
import json
from uuid import uuid4

import httpx
from fastapi import HTTPException
from open_webui.models.config import Config
from open_webui.utils.oi import catalog
from open_webui.utils.oi.endpoints import resolve_endpoint
from open_webui.utils.oi.protocol_registry import canonical_protocol


async def require_browser_owner(connection, user):
    if (
        connection.transport != 'browser'
        or connection.owner_id != user.id
        or not connection.enabled
        or (connection.settings or {}).get('deleted')
    ):
        raise HTTPException(404, 'Browser connection unavailable', headers={'Cache-Control': 'no-store'})
    if not await Config.get('direct.enable'):
        raise HTTPException(403, 'Browser connections are disabled', headers={'Cache-Control': 'no-store'})


def require_live_session(metadata, user):
    from open_webui.socket.main import SESSION_POOL

    session = SESSION_POOL.get(metadata.get('session_id')) if metadata.get('session_id') else None
    if not session or session.get('id') != user.id:
        raise HTTPException(
            422, 'Browser connections require your active browser; choose a server connection for background tasks'
        )


class BrowserStream(httpx.AsyncByteStream):
    def __init__(self, transport, queue):
        self.transport = transport
        self.queue = queue

    async def __aiter__(self):
        try:
            while True:
                require_live_session(self.transport.metadata, self.transport.user)
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=2)
                except TimeoutError:
                    continue
                if isinstance(item, dict):
                    if item.get('error'):
                        raise httpx.ReadError('Browser provider stream interrupted')
                    if item.get('done'):
                        break
                    item = json.dumps(item)
                yield item.encode() if isinstance(item, str) else item
        finally:
            await self.transport.aclose()


class BrowserTransport(httpx.AsyncBaseTransport):
    def __init__(self, user, metadata, binding, connection):
        self.user = user
        self.metadata = {**metadata, 'user_id': user.id}
        self.binding = binding
        self.connection = connection
        self.channel = None

    async def handle_async_request(self, request):
        from open_webui.socket.main import EVENT_QUEUES, get_event_call

        await require_browser_owner(self.connection, self.user)
        require_live_session(self.metadata, self.user)
        self.channel = f'{self.user.id}:{self.metadata["session_id"]}:{uuid4()}'
        queue = asyncio.Queue()
        EVENT_QUEUES[self.channel] = queue
        stream = bool(request.extensions.get('oi_stream', False))
        expected = resolve_endpoint(
            self.connection, self.binding.protocol, model=self.binding.upstream_id, stream=stream
        )
        if request.method != 'POST' or str(request.url) != expected:
            await self.aclose()
            raise HTTPException(422, 'Browser request endpoint mismatch')
        caller = await get_event_call(self.metadata)
        payload = json.loads(request.content)
        try:
            result = await caller(
                {
                    'type': 'request:chat:completion',
                    'data': {
                        'channel': self.channel,
                        'session_id': self.metadata['session_id'],
                        'form_data': payload,
                        'url': str(request.url),
                        'protocol': canonical_protocol(self.binding.protocol),
                        'stream': stream,
                        'protocol_headers': {
                            key: value
                            for key, value in request.headers.items()
                            if key.lower().startswith('x-rp-') or key.lower() == 'anthropic-version'
                        },
                        'model': {
                            'id': self.binding.id,
                            'direct': True,
                            'connection': {
                                'id': self.connection.id,
                                'transport': 'browser',
                                'revision': self.connection.revision,
                            },
                        },
                    },
                }
            )
            if not isinstance(result, dict) or not result.get('status_code'):
                raise httpx.ConnectError('Browser request did not acknowledge a provider response')
            status = result['status_code']
            headers = {
                key: value
                for key, value in (result.get('headers') or {}).items()
                if key.lower() in {'content-type', 'retry-after', 'x-request-id'}
            }
            if stream and status < 400:
                return httpx.Response(status, headers=headers, stream=BrowserStream(self, queue))
            await self.aclose()
            return httpx.Response(status, headers=headers, content=result.get('body', ''))
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self):
        from open_webui.socket.main import EVENT_QUEUES, sio

        if self.channel is None:
            return
        channel, self.channel = self.channel, None
        EVENT_QUEUES.pop(channel, None)
        await sio.emit(
            'events',
            {
                'chat_id': self.metadata.get('chat_id'),
                'message_id': self.metadata.get('message_id'),
                'data': {
                    'type': 'request:chat:cancel',
                    'data': {'channel': channel, 'session_id': self.metadata['session_id']},
                },
            },
            to=self.metadata['session_id'],
        )
