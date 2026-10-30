"""Client-held authenticated native history. No temporary text is persisted."""

import base64
import copy
import hashlib
import json
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from open_webui.env import WEBUI_SECRET_KEY


def cipher():
    if not WEBUI_SECRET_KEY:
        raise ValueError('A persistent server secret is required')
    return Fernet(
        base64.urlsafe_b64encode(hashlib.sha256(('oi.temporary.native.v1:' + WEBUI_SECRET_KEY).encode()).digest())
    )


def digest(projection):
    return hashlib.sha256(
        json.dumps(projection, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()


def seal(calls, projection, user_id, temporary_id, message_id):
    return (
        cipher()
        .encrypt(
            json.dumps(
                {
                    'user': user_id,
                    'temporary': temporary_id,
                    'message': message_id,
                    'projection': digest(projection),
                    'calls': calls,
                },
                ensure_ascii=False,
            ).encode()
        )
        .decode()
    )


def projection(message):
    return (
        message['output']
        if isinstance(message.get('output'), list)
        else {
            key: message[key]
            for key in ('role', 'content', 'reasoning_content', 'tool_calls', 'reasoning_details')
            if key in message
        }
    )


def restore(message, user_id, temporary_id):
    capsule = message.pop('oi_native_state', None)
    message.pop('oi_native', None)  # Client-supplied opaque envelopes are not trusted.
    if not capsule or message.get('oi_native_invalidated'):
        return []
    try:
        if not isinstance(capsule, str) or len(capsule) > 16 * 1024 * 1024:
            raise ValueError()
        data = json.loads(cipher().decrypt(capsule.encode()))
        if (data['user'], data['temporary'], data['message']) != (user_id, temporary_id, message.get('id')):
            raise ValueError()
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise HTTPException(403, 'Native continuation state is invalid for this conversation') from None
    if data['projection'] != digest(projection(message)):
        message['oi_native_invalidated'] = True
        return []
    calls = copy.deepcopy(data['calls'])
    message['oi_native'] = calls
    return calls


def restore_request(request, messages, metadata, user):
    history = {}
    for message in messages:
        calls = restore(message, user.id, metadata.get('oi_temporary_id'))
        if calls:
            history[message.get('id')] = calls
    if history:
        request.state.oi_temporary_native_history = history


def response_state(request, metadata, user, projection):
    history = getattr(request.state, 'oi_temporary_native_history', {})
    calls = copy.deepcopy(history.get(metadata.get('message_id'), []))
    key = metadata.get('oi_generation_id') or metadata.get('message_id') or 'api'
    for native in getattr(request.state, 'oi_native_call_history', {}).get(key, []):
        calls = [c for c in calls if c['call_id'] != native['call_id']]
        calls.append(copy.deepcopy(native))
    if not calls or not metadata.get('oi_temporary_id'):
        return None
    return seal(calls, projection, user.id, metadata['oi_temporary_id'], metadata.get('message_id'))


def wrap_emitter(emitter, request, metadata, user):
    async def emit(event):
        data = event.get('data') or {}
        if event.get('type') == 'chat:completion' and data.get('done') and isinstance(data.get('output'), list):
            capsule = response_state(request, metadata, user, data['output'])
            if capsule:
                event = {**event, 'data': {**data, 'oi_native_state': capsule}}
        return await emitter(event)

    return emit
