"""Connection-scoped non-chat model calls using the same permission and billing boundary."""

import copy
import base64
import json
import math
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException
from open_webui.internal.db import get_async_db
from open_webui.utils.models import check_model_access
from open_webui.utils.oi import catalog, runtime
from open_webui.utils.oi.model_targets import resolve_native_target
from open_webui.utils.oi.endpoints import resolve_endpoint
from starlette.responses import Response

OPENAI = {'openai-chat', 'openai-responses'}


async def billing_context(raw, user):
    """Client attribution can name its chat/message, but can never select an existing generation."""
    from open_webui.models.chats import Chats
    from open_webui.utils.chat_id import is_saved_chat_id, get_temporary_chat_session_id

    if not isinstance(raw, dict):
        return {}
    context = {
        key: raw[key] for key in ('chat_id', 'message_id') if isinstance(raw.get(key), str) and len(raw[key]) <= 200
    }
    chat_id = context.get('chat_id')
    if chat_id and is_saved_chat_id(chat_id):
        chat = await Chats.get_chat_by_id(chat_id)
        if chat is None or chat.user_id != user.id:
            raise HTTPException(403, 'Conversation billing is unavailable')
    elif chat_id:
        temporary = get_temporary_chat_session_id(chat_id)
        if temporary:
            context['oi_temporary_id'] = temporary
        else:
            context.pop('chat_id')
    return context


async def target_for(reference, capability, user=None):
    if not isinstance(reference, str) or not reference:
        raise HTTPException(422, 'Select a connection model')
    active = runtime.active_generation.get()
    user = user or (active[0] if active else None)
    if user is None:
        raise HTTPException(403, 'A user context is required for a connection model')
    async with get_async_db() as db:
        target = await resolve_native_target(db, reference, user)
        if target is None or not await catalog.binding_readable(db, target.binding, target.connection, user):
            raise HTTPException(404, 'Connection model unavailable')
        if target.connection.transport == 'browser':
            raise HTTPException(
                422, 'This capability requires a server connection; browser connections only support interactive chat'
            )
        try:
            await check_model_access(user, {'id': reference, 'oi_binding': reference.startswith('oi:')}, db=db)
        except Exception:
            raise HTTPException(403, 'Connection model access denied') from None
        if catalog.capabilities(target.binding).get(capability) is not True:
            raise HTTPException(422, f'The selected connection model has no confirmed {capability} capability')
    return target, user


def endpoint(target, path):
    try:
        return resolve_endpoint(
            target.connection, target.binding.protocol, 'capability', model=target.binding.upstream_id, path=path
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


def parameters(target, capability):
    value = (target.params.get('capability_params') or {}).get(capability, {})
    if not isinstance(value, dict):
        raise HTTPException(422, 'Capability parameters must be an object')
    return copy.deepcopy(value)


async def request(
    target,
    user,
    reference,
    capability,
    path,
    body=None,
    files=None,
    request=None,
    metadata=None,
    transport=None,
    binary=False,
    validate=None,
):
    active = runtime.active_generation.get()
    source = (
        metadata
        or getattr(getattr(request, 'state', None), 'metadata', {})
        or (active[1] if active and active[0].id == user.id else {})
    )
    context = {
        key: source[key]
        for key in ('chat_id', 'message_id', 'oi_generation_id', 'oi_temporary_id', 'session_id')
        if key in source
    }
    if context.get('chat_id') and not context.get('oi_generation_id') and not context.get('oi_temporary_id'):
        from open_webui.models.chats import Chats

        chat = await Chats.get_chat_by_id(context['chat_id'])
        if chat is None or chat.user_id != user.id:
            raise HTTPException(403, 'Conversation billing is unavailable')
    # Attribution identifiers come from the verified request/task, never from capability payloads.
    context['task'] = capability
    form = {'model': reference, 'metadata': context, 'messages': []}
    adapter_request = request or SimpleNamespace(state=SimpleNamespace(metadata=context))
    headers = catalog.request_headers(
        target.connection,
        user_id=user.id,
        chat_id=context.get('oi_temporary_id') or context.get('chat_id') or f'capability:{uuid4()}',
        protocol=target.binding.protocol,
    )
    if not files:
        headers['Content-Type'] = 'application/json'
    else:
        headers = {key: value for key, value in headers.items() if key.lower() != 'content-type'}

    async def dispatch(call_id):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(180, connect=30), trust_env=True, transport=transport
        ) as client:
            response = await client.post(
                endpoint(target, path), headers=headers, **({'data': body, 'files': files} if files else {'json': body})
            )
        if response.status_code >= 400:
            raise HTTPException(
                response.status_code,
                {
                    'code': 'connection_capability_failed',
                    'capability': capability,
                    'upstream_status': response.status_code,
                },
                headers={'Retry-After': response.headers['retry-after']} if 'retry-after' in response.headers else None,
            )
        if binary:
            return Response(
                response.content, media_type=response.headers.get('content-type', 'application/octet-stream')
            )
        result = response.json()
        if not isinstance(result, dict):
            raise HTTPException(502, 'The provider returned an invalid capability response')
        if target.binding.protocol in {'gemini', 'gemini-interactions', 'cpa-agy'}:
            result['oi_usage'] = result.get('usageMetadata') or {}
            result['oi_usage_protocol'] = 'gemini'
        elif target.binding.protocol == 'ollama':
            result['oi_usage'] = {key: result[key] for key in ('prompt_eval_count', 'eval_count') if key in result}
        if capability == 'embedding':
            result['oi_usage'] = result.get('oi_usage') or result.get('usage') or {}
            result['oi_usage_protocol'] = 'embedding'
        return result

    return await runtime.track_call(
        adapter_request, form, user, dispatch, target.binding, target.connection, validate_result=validate
    )


async def embeddings(
    reference, texts, user=None, transport=None, extra=None, public_response=False, encoding_format='float'
):
    target, user = await target_for(reference, 'embedding', user)
    if isinstance(texts, str):
        texts = [texts]
    if not isinstance(texts, list) or not texts:
        raise HTTPException(422, 'Embedding input must be nonempty')
    token_ids = all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in texts)
    count = 1 if token_ids else len(texts)
    if target.binding.protocol not in OPENAI and not all(isinstance(x, str) for x in texts):
        raise HTTPException(422, 'This embedding protocol requires text input')
    if encoding_format not in {'float', 'base64'}:
        raise HTTPException(422, 'Unsupported embedding encoding')
    options = {**parameters(target, 'embedding'), **(extra or {})}
    protocol = target.binding.protocol
    model = target.binding.upstream_id
    if protocol in OPENAI:
        path = '/embeddings'
        body = {**options, 'model': model, 'input': texts, 'encoding_format': 'float'}
    elif protocol == 'ollama':
        path = '/api/embed'
        body = {**options, 'model': model, 'input': texts, 'truncate': False}
    elif protocol in {'gemini', 'gemini-interactions', 'cpa-agy'}:
        path = f'/models/{quote(model, safe="")}:batchEmbedContents'
        body = {
            'requests': [
                {**options, 'model': f'models/{model}', 'content': {'parts': [{'text': text}]}} for text in texts
            ]
        }
    else:
        raise HTTPException(422, 'This protocol has no embedding adapter')
    result = await request(
        target,
        user,
        reference,
        'embedding',
        path,
        body,
        transport=transport,
        validate=lambda result: embedding_vectors(result, protocol, count),
    )
    vectors = embedding_vectors(result, protocol, count)
    if not public_response:
        return vectors
    if encoding_format == 'base64':
        import struct

        vectors = [base64.b64encode(struct.pack('<' + 'f' * len(v), *v)).decode() for v in vectors]
    return {
        'object': 'list',
        'model': reference,
        'data': [{'object': 'embedding', 'index': i, 'embedding': v} for i, v in enumerate(vectors)],
        'usage': result.get('usage') or {},
    }


def embedding_vectors(result, protocol, count):
    if protocol in OPENAI:
        rows = result.get('data') or []
        if (
            len(rows) != count
            or any(not isinstance(row, dict) for row in rows)
            or {row.get('index') for row in rows} != set(range(count))
        ):
            raise HTTPException(502, 'Embedding indices do not match the input batch')
        vectors = [row.get('embedding') for row in sorted(rows, key=lambda row: row['index'])]
    else:
        vectors = (
            [item.get('values') for item in result.get('embeddings', [])]
            if protocol != 'ollama'
            else result.get('embeddings', [])
        )
    if len(vectors) != count or any(
        not isinstance(v, list)
        or not v
        or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in v)
        for v in vectors
    ):
        raise HTTPException(502, 'The provider returned missing or invalid embeddings')
    if len({len(v) for v in vectors}) != 1:
        raise HTTPException(502, 'Embedding dimensions differ within the batch')
    return vectors


async def rerank(reference, query, documents, user=None, transport=None):
    target, user = await target_for(reference, 'rerank', user)
    if target.binding.protocol not in OPENAI:
        raise HTTPException(422, 'This protocol has no reranking adapter')
    path = target.connection.settings.get('rerank_path') or '/rerank'
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
        raise HTTPException(422, 'Reranker path must be a relative API path')
    body = {
        **parameters(target, 'rerank'),
        'model': target.binding.upstream_id,
        'query': query,
        'documents': documents,
        'top_n': len(documents),
    }
    result = await request(
        target,
        user,
        reference,
        'rerank',
        path,
        body,
        transport=transport,
        validate=lambda result: rerank_scores(result, len(documents)),
    )
    return rerank_scores(result, len(documents))


def rerank_scores(result, count):
    rows = result.get('results') or []
    if (
        len(rows) != count
        or any(not isinstance(row, dict) for row in rows)
        or {row.get('index') for row in rows} != set(range(count))
    ):
        raise HTTPException(502, 'Reranking indices do not match the documents')
    scores = [row.get('relevance_score') for row in sorted(rows, key=lambda row: row['index'])]
    if any(
        isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) for score in scores
    ):
        raise HTTPException(502, 'Reranker returned invalid scores')
    return scores


async def speech(reference, payload, user, request_object=None, transport=None):
    target, user = await target_for(reference, 'tts', user)
    if target.binding.protocol not in OPENAI:
        raise HTTPException(422, 'This protocol has no speech adapter')
    body = {**parameters(target, 'tts'), **payload, 'model': target.binding.upstream_id}
    if body.get('stream_format') not in {None, 'audio'}:
        raise HTTPException(422, 'The speech playback endpoint requires audio stream format')
    body['stream_format'] = 'audio'
    return await request(
        target, user, reference, 'tts', '/audio/speech', body, request=request_object, transport=transport, binary=True
    )


async def transcription(reference, file_name, content, user, language=None, request_object=None, transport=None):
    target, user = await target_for(reference, 'stt', user)
    if target.binding.protocol not in OPENAI:
        raise HTTPException(422, 'This protocol has no transcription adapter')
    body = {**parameters(target, 'stt'), 'model': target.binding.upstream_id, 'response_format': 'json'}
    if language:
        body['language'] = language
    return await request(
        target,
        user,
        reference,
        'stt',
        '/audio/transcriptions',
        body,
        files={'file': (file_name, content)},
        request=request_object,
        transport=transport,
        validate=transcription_text,
    )


def transcription_text(result):
    if not isinstance(result.get('text'), str):
        raise HTTPException(502, 'Speech provider returned no transcription')


def image_outputs(result):
    outputs = [item.get('url') or item.get('b64_json') for item in result.get('data', []) if isinstance(item, dict)]
    outputs += [item.get('bytesBase64Encoded') for item in result.get('predictions', []) if isinstance(item, dict)]
    for candidate in result.get('candidates', []):
        for part in (candidate.get('content') or {}).get('parts', []):
            inline = part.get('inlineData') or part.get('inline_data') or {}
            if inline.get('data'):
                outputs.append(
                    f'data:{inline.get("mimeType") or inline.get("mime_type") or "image/png"};base64,{inline["data"]}'
                )
    if not outputs or any(not isinstance(item, str) or not item for item in outputs):
        raise HTTPException(502, 'Image provider returned no usable images')
    return outputs


async def images(reference, payload, user, *, input_images=None, request_object=None, metadata=None, transport=None):
    capability = 'image_edit' if input_images is not None else 'image_generation'
    target, user = await target_for(reference, capability, user)
    options = parameters(target, capability)
    model = target.binding.upstream_id
    protocol = target.binding.protocol
    files = None
    if protocol in OPENAI:
        body = {**options, **payload, 'model': model}
        path = '/images/edits' if input_images is not None else '/images/generations'
        if input_images is not None:
            files = []
            for i, value in enumerate(input_images):
                try:
                    header, data = value.split(',', 1)
                    mime = header.removeprefix('data:').split(';')[0]
                    if not mime.startswith('image/') or ';base64' not in header:
                        raise ValueError()
                    content = base64.b64decode(data, validate=True)
                except (ValueError, TypeError):
                    raise HTTPException(422, 'Image edits require valid image data') from None
                files.append(('image[]' if len(input_images) > 1 else 'image', (f'image-{i}', content, mime)))
            body = {
                key: json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
                for key, value in body.items()
                if value is not None
            }
    elif protocol in {'gemini', 'gemini-interactions', 'cpa-agy'}:
        method = options.pop('endpoint_method', 'generateContent')
        if method == 'predict' and input_images is None:
            body = {
                **options,
                'instances': [{'prompt': payload['prompt']}],
                'parameters': {**options.get('parameters', {}), 'sampleCount': payload.get('n', 1)},
            }
        elif method == 'generateContent':
            if payload.get('n', 1) != 1:
                raise HTTPException(422, 'GenerateContent accepts one image request at a time')
            parts = [{'text': payload['prompt']}]
            for value in input_images or []:
                try:
                    header, data = value.split(',', 1)
                    mime = header.removeprefix('data:').split(';')[0]
                    if not mime.startswith('image/') or ';base64' not in header:
                        raise ValueError()
                    base64.b64decode(data, validate=True)
                except (ValueError, TypeError):
                    raise HTTPException(422, 'Image edits require valid image data') from None
                parts.append({'inlineData': {'mimeType': mime, 'data': data}})
            body = {**options, 'contents': [{'role': 'user', 'parts': parts}]}
        else:
            raise HTTPException(422, 'Unsupported image endpoint method')
        path = f'/models/{quote(model, safe="")}:{method}'
    else:
        raise HTTPException(422, 'This protocol has no image adapter')
    result = await request(
        target,
        user,
        reference,
        capability,
        path,
        body,
        files=files,
        request=request_object,
        metadata=metadata,
        transport=transport,
        validate=image_outputs,
    )
    return image_outputs(result), target
