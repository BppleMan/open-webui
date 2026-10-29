"""Connection identity and per-response/ASR accounting for the existing voice bridge."""

import asyncio
import copy
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException
from open_webui.internal.db import get_async_db
from open_webui.utils.oi import accounting, catalog
from open_webui.utils.oi.endpoints import api_base, compatibility_profile
from open_webui.utils.oi.capability_calls import OPENAI, target_for


async def connection(config, user, chat_id):
    voice, _ = await target_for(config['audio.realtime.model'], 'realtime', user)
    transcription, _ = await target_for(config['audio.realtime.transcription_model'], 'stt', user)
    if voice.binding.protocol not in OPENAI or transcription.connection.id != voice.connection.id:
        raise HTTPException(422, 'Realtime and transcription models must use the same compatible connection')
    url = urlsplit(
        voice.connection.base_url
        if compatibility_profile(voice.connection) == 'azure-openai'
        else api_base(voice.connection, voice.binding.protocol)
    )
    if compatibility_profile(voice.connection) == 'azure-openai':
        settings = voice.connection.settings
        if not settings.get('api_version'):
            raise HTTPException(422, 'Azure Realtime requires an explicit API version')
        path = url.path.rstrip('/') + '/openai/realtime'
        query = {
            'deployment': settings.get('deployment') or voice.binding.upstream_id,
            'api-version': settings['api_version'],
        }
    else:
        path = url.path.rstrip('/') + '/realtime'
        query = {'model': voice.binding.upstream_id}
    ws_url = urlunsplit(('wss' if url.scheme == 'https' else 'ws', url.netloc, path, urlencode(query), ''))
    headers = catalog.request_headers(
        voice.connection, user_id=user.id, chat_id=chat_id, protocol=voice.binding.protocol
    )
    return ws_url, headers, voice, transcription


class CallLedger:
    def __init__(self, user, chat_id, voice, transcription):
        self.user = user
        self.session_id = str(uuid4())
        self.chat_id = chat_id or 'temporary:voice:' + self.session_id
        self.voice = voice
        self.transcription = transcription
        self.entries = {}
        self.responses = {}
        self.lock = asyncio.Lock()

    async def begin(self, key, purpose, target):
        call_id = str(uuid5(NAMESPACE_URL, f'oi-voice:{self.user.id}:{self.session_id}:{key}'))
        async with self.lock:
            if call_id in self.entries:
                return call_id
            async with get_async_db() as db:
                generation, _ = await accounting.begin_generation(
                    db, self.user.id, self.chat_id, call_id, call_id, purpose
                )
                await accounting.begin_call(
                    db,
                    generation,
                    target.binding.id,
                    binding_id=target.binding.id,
                    connection_id=target.connection.id,
                    pricing=copy.deepcopy(target.binding.pricing),
                    call_id=call_id,
                )
                await db.commit()
                self.entries[call_id] = {
                    'generation': generation.id,
                    'purpose': purpose,
                    'done': False,
                    'target': target,
                }
        return call_id

    async def prepare(self, command):
        if command.get('type') == 'response.create':
            command = copy.deepcopy(command)
            call_id = await self.begin('response:' + str(uuid4()), 'realtime', self.voice)
            response = command.setdefault('response', {})
            response['metadata'] = {**response.get('metadata', {}), 'oi_call_id': call_id}
        return command

    async def settle(self, call_id, raw=None, status='completed', ending=None, *, final=True):
        async with self.lock:
            entry = self.entries.get(call_id)
            if entry is None or entry['done']:
                return
            raw = {**entry.get('raw_usage', {}), **(raw or {})}
            entry['raw_usage'] = raw
            usage = accounting.normalize_usage(raw, 'realtime' if entry['purpose'] == 'realtime' else 'openai')
            usage['context'] = {'purpose': entry['purpose']}
            if ending:
                usage['finish'] = ending
            actual = None
            connection = entry['target'].connection
            if raw and (
                compatibility_profile(connection) == 'openrouter' or connection.settings.get('cost_currency') == 'USD'
            ):
                try:
                    if raw.get('cost') is not None:
                        actual = accounting.money(raw['cost'])
                except ValueError:
                    pass
            async with get_async_db() as db:
                await accounting.settle_call(
                    db,
                    call_id,
                    usage,
                    actual_usd=actual,
                    source='provider:usage.cost:USD' if actual is not None else None,
                    status=status,
                )
                if final:
                    await accounting.finish_generation(db, entry['generation'], status)
                await db.commit()
            entry['done'] = final

    async def observe(self, event):
        kind = event.get('type', '')
        if kind in {'input_audio_buffer.speech_started', 'input_audio_buffer.committed'}:
            if event.get('item_id'):
                await self.begin('stt:' + event['item_id'], 'stt', self.transcription)
        elif kind in {
            'conversation.item.input_audio_transcription.completed',
            'conversation.item.input_audio_transcription.failed',
        }:
            call_id = await self.begin('stt:' + event['item_id'], 'stt', self.transcription)
            await self.settle(call_id, event.get('usage'), 'failed' if kind.endswith('.failed') else 'completed')
        elif kind.startswith('response.') and isinstance(event.get('response'), dict):
            response = event.get('response') or {}
            response_id = response.get('id')
            if not response_id:
                return
            call_id = self.responses.get(response_id) or (response.get('metadata') or {}).get('oi_call_id')
            if call_id not in self.entries:
                # Older compatible gateways may omit response metadata; claim one outstanding request.
                waiting = [
                    key
                    for key, value in self.entries.items()
                    if value['purpose'] == 'realtime' and not value['done'] and key not in self.responses.values()
                ]
                call_id = (
                    waiting[0]
                    if len(waiting) == 1
                    else await self.begin('provider:' + response_id, 'realtime', self.voice)
                )
            self.responses[response_id] = call_id
            if kind != 'response.done' and response.get('usage'):
                await self.settle(call_id, response['usage'], 'running', final=False)
            if kind == 'response.done':
                state = response.get('status')
                status = state if state in {'completed', 'failed', 'cancelled', 'incomplete'} else 'interrupted'
                await self.settle(
                    call_id,
                    response.get('usage'),
                    status,
                    {'status': status, 'provider_reason': (response.get('status_details') or {}).get('reason')},
                )

    async def close(self):
        for call_id, entry in list(self.entries.items()):
            if not entry['done']:
                await self.settle(call_id, status='interrupted')
