"""Accounting at the provider-call boundary, independent of display history."""

import asyncio
import codecs
import copy
import json
import time
from contextvars import ContextVar
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException
from open_webui.internal.db import get_async_db
from open_webui.models.customization import Generation, ModelCall
from open_webui.utils.oi import accounting
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

# Carries attribution through async retrieval/tool calls which have no Request argument.
active_generation = ContextVar('oi_active_generation', default=None)


def fork_request(request, metadata):
    # Each simultaneous model owns its own state. The application/pool stays shared.
    scope = {**request.scope, 'state': {**request.scope.get('state', {}), 'metadata': metadata}}
    return Request(scope, receive=request.receive)


class SSEDecoder:
    """Incremental UTF-8/SSE parser; handles arbitrary network chunk boundaries."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder('utf-8')()
        self.buffer = ''
        self.data = []

    def feed(self, chunk, final=False):
        self.buffer += self.decoder.decode(chunk, final=final) if isinstance(chunk, bytes) else chunk
        if final:
            self.buffer += '\n\n'
        events = []
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            line = line.rstrip('\r')
            if not line:
                if self.data:
                    raw = '\n'.join(self.data)
                    self.data = []
                    if raw != '[DONE]':
                        try:
                            value = json.loads(raw)
                            if isinstance(value, dict):
                                events.append(value)
                        except json.JSONDecodeError:
                            pass
            elif line.startswith('data:'):
                self.data.append(line[5:].lstrip(' '))
        return events


async def start_generation(user, metadata, model_id):
    chat_id = (
        ('temporary:' + metadata['oi_temporary_id'])
        if metadata.get('oi_temporary_id')
        else (metadata.get('chat_id') or 'api:' + str(uuid4()))
    )
    message_id = metadata.get('message_id') or str(uuid4())
    request_id = metadata.get('oi_request_id') or message_id
    # Each parallel model response is a generation, even when models have equal names.
    request_id = f'{request_id}:{message_id}'
    async with get_async_db() as db:
        generation, created = await accounting.begin_generation(
            db,
            user.id,
            chat_id,
            message_id,
            request_id,
            'continue' if metadata.get('assistant_message_id') else metadata.get('task', 'assistant'),
        )
        await db.commit()
        if not created:
            raise HTTPException(
                409,
                {
                    'code': 'generation_already_accepted',
                    'generation_id': generation.id,
                    'status': generation.status,
                    'chat_id': chat_id,
                },
            )
        metadata['oi_generation_id'] = generation.id
    return generation.id


async def end_generation(generation_id, status='completed'):
    async with get_async_db() as db:
        if status == 'completed':
            last = (
                await db.execute(
                    select(ModelCall)
                    .where(ModelCall.generation_id == generation_id)
                    .order_by(ModelCall.sequence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last is not None and last.status != 'completed':
                status = last.status
        snapshot = await accounting.finish_generation(db, generation_id, status)
        await db.commit()
        return snapshot.id


async def reserve_generations(user, metadata, entries):
    """Claim the entire fan-out before any message placeholder or chat mutation."""
    chat_id = (
        ('temporary:' + metadata['oi_temporary_id'])
        if metadata.get('oi_temporary_id')
        else (metadata.get('chat_id') or 'api:' + str(uuid4()))
    )
    reserved = {}
    async with get_async_db() as db:
        for index, entry in enumerate(entries):
            message_id = entry.get('message_id') or str(
                uuid5(NAMESPACE_URL, f'oi:{user.id}:{metadata["oi_request_id"]}:{index}')
                if metadata.get('oi_request_id')
                else uuid4()
            )
            entry['message_id'] = message_id
            request_id = f'{metadata.get("oi_request_id") or message_id}:{message_id}'
            try:
                generation, created = await accounting.begin_generation(
                    db,
                    user.id,
                    chat_id,
                    message_id,
                    request_id,
                    'continue' if metadata.get('assistant_message_id') else metadata.get('task', 'assistant'),
                )
            except ValueError:
                generation = (
                    await db.execute(
                        select(Generation).where(Generation.user_id == user.id, Generation.request_id == request_id)
                    )
                ).scalar_one_or_none()
                created = False
            if not created or message_id in reserved:
                # No partial batch is committed if one member is a retry or duplicate.
                conflict = {
                    'code': 'generation_already_accepted',
                    'generation_id': generation.id if generation else None,
                    'status': generation.status if generation else 'conflict',
                    'chat_id': generation.chat_id if generation else chat_id,
                }
                await db.rollback()
                raise HTTPException(409, conflict)
            reserved[message_id] = generation.id
        await db.commit()
    return reserved


async def track_call(request, form, user, dispatch, binding=None, connection=None, *, validate_result=None):
    metadata = form.get('metadata') or getattr(request.state, 'metadata', {}) or {}
    generation_id = metadata.get('oi_generation_id')
    standalone = not generation_id
    if standalone:
        # Task/API callers that do not enter main.process_chat still get independent records.
        metadata = dict(metadata, oi_request_id=str(uuid4()))
        generation_id = await start_generation(user, metadata, form.get('model'))
    async with get_async_db() as db:
        generation = await db.get(Generation, generation_id)
        if generation is None or generation.user_id != user.id:
            raise HTTPException(403, 'Invalid generation owner')
        call, _ = await accounting.begin_call(
            db,
            generation,
            form.get('model', ''),
            binding_id=binding.id if binding else None,
            connection_id=connection.id if connection else None,
            pricing=copy.deepcopy(binding.pricing) if binding else {},
        )
        call_id = call.id
        await db.commit()  # Durable before contacting any provider.

    from open_webui.utils.oi.catalog import capabilities
    from open_webui.utils.oi.context import observe_context

    context = observe_context(form, capabilities(binding) if binding else {})
    if connection is not None:
        context['transport'] = getattr(connection, 'transport', None) or 'server'
    started, first_token = time.monotonic(), None
    usage, actual, source, status, ending = {}, None, None, 'interrupted', None

    def observe(data):
        nonlocal usage, actual, source, status, first_token, ending
        if first_token is None and (
            data.get('type', '').endswith('.delta')
            or any(c.get('delta', {}).get('content') for c in data.get('choices', []))
        ):
            first_token = round((time.monotonic() - started) * 1000)
        raw = data.get('oi_usage') or data.get('usage') or (data.get('response') or {}).get('usage')
        if raw:
            # Every provider adapter supplies cumulative snapshots, never unconditional sums.
            usage = accounting.normalize_usage(raw, data.get('oi_usage_protocol', 'openai'))
            supplied_cost = (
                raw.get('cost')
                if connection
                and (connection.template == 'openrouter' or connection.settings.get('cost_currency') == 'USD')
                else data.get('oi_actual_usd')
            )
            if supplied_cost is not None:
                try:
                    actual = accounting.money(supplied_cost)
                    source = 'provider:usage.cost:USD'
                except ValueError:
                    pass
        if data.get('oi_finish'):
            ending = copy.deepcopy(data['oi_finish'])
            status = ending['status']
        if data.get('error') or data.get('type') in {'response.failed', 'error'}:
            status = 'failed'
        elif data.get('type') == 'response.incomplete':
            status = 'incomplete'
        elif data.get('type') == 'response.completed' or any(c.get('finish_reason') for c in data.get('choices', [])):
            if not ending and status != 'failed':
                status = 'completed'

    async def settle():
        async with get_async_db() as db:
            await accounting.settle_call(
                db,
                call_id,
                {
                    **usage,
                    'context': context,
                    'duration_ms': round((time.monotonic() - started) * 1000),
                    'first_token_ms': first_token,
                    **({'finish': ending} if ending else {}),
                },
                actual_usd=actual,
                source=source,
                status=status,
            )
            await db.commit()
        if standalone:
            await end_generation(generation_id, status)

    try:
        result = await dispatch(call_id)
    except BaseException:
        status = 'failed'
        await asyncio.shield(settle())
        raise
    if isinstance(result, StreamingResponse):

        async def stream():
            nonlocal status
            decoder = SSEDecoder()
            try:
                async for chunk in result.body_iterator:
                    for data in decoder.feed(chunk):
                        observe(data)
                    yield chunk
                for data in decoder.feed(b'', final=True):
                    observe(data)
            except asyncio.CancelledError:
                if status == 'interrupted':
                    status = 'cancelled'
                raise
            except BaseException:
                status = 'failed'
                raise
            finally:
                await asyncio.shield(settle())

        return StreamingResponse(
            stream(),
            status_code=result.status_code,
            headers={k: v for k, v in result.headers.items() if k.lower() != 'content-length'},
            background=result.background,
        )
    if isinstance(result, dict):
        observe(result)
        if validate_result is not None:
            try:
                validate_result(result)
            except BaseException:
                status = 'failed'
                await asyncio.shield(settle())
                raise
        if status == 'interrupted':
            status = 'completed'
    elif isinstance(result, Response):
        status = 'failed' if result.status_code >= 400 else 'completed'
        try:
            observe(json.loads(result.body))
        except (ValueError, TypeError):
            pass
    await settle()
    return result
