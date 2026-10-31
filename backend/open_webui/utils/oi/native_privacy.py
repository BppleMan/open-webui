"""Separate caller-private provider metadata from portable protocol replay."""

import copy
from sqlalchemy import select
from open_webui.internal.db import get_async_db_context
from open_webui.models.customization import Connection, Generation, ModelCall


def replay_envelope(envelope):
    """Only assistant protocol outputs can cross an ownership boundary.

    Response-level instructions, request options and continuation IDs are not
    conversation output. Keep structured output blocks/signatures verbatim.
    """
    if not isinstance(envelope, dict) or not isinstance(envelope.get('body'), dict):
        return None
    body, protocol = envelope['body'], envelope.get('protocol')
    if protocol == 'openai-responses':
        projected = {'output': copy.deepcopy(body.get('output', []))}
    elif protocol == 'anthropic':
        projected = {'content': copy.deepcopy(body.get('content', []))}
    elif protocol in {'gemini', 'cpa-agy'}:
        projected = {
            'candidates': [{'content': copy.deepcopy(c.get('content', {}))} for c in body.get('candidates', [])]
        }
    elif protocol == 'gemini-interactions':
        projected = {key: copy.deepcopy(body[key]) for key in ('steps', 'outputs') if key in body}
    elif protocol in {'openai-chat', 'ollama'}:
        message = body.get('message') if protocol == 'ollama' else (body.get('choices') or [{}])[0].get('message')
        message = message or {}
        safe = {
            k: copy.deepcopy(message[k])
            for k in (
                'role',
                'content',
                'thinking',
                'reasoning_content',
                'reasoning_details',
                'tool_calls',
                'function_call',
                'refusal',
                'audio',
                'images',
                'annotations',
            )
            if k in message
        }
        extra = message.get('provider_specific_fields')
        if isinstance(extra, dict) and 'reasoning_details' in extra:
            safe['provider_specific_fields'] = {'reasoning_details': copy.deepcopy(extra['reasoning_details'])}
        projected = {'message': safe} if protocol == 'ollama' else {'choices': [{'message': safe}]}
    else:
        return None
    return {
        **{k: copy.deepcopy(envelope[k]) for k in ('call_id', 'binding_id', 'protocol', 'replayable') if k in envelope},
        'body': projected,
        'replay_projection': True,
    }


async def project_native(body, owner_id, viewer_id, db=None, *, portable=False):
    """Mutate a detached response/copy, never the trusted stored conversation.

    The call ledger is authoritative even after edits reattribute a message.
    Legacy calls without a ledger use the original message author. A shared
    connection's private configuration remains its owner's, not its caller's.
    """
    messages = list((body.get('history', {}).get('messages') or {}).values()) + list(body.get('messages') or [])
    ids = {
        n.get('call_id') for m in messages for n in m.get('oi_native', []) if isinstance(n, dict) and n.get('call_id')
    }
    ownership = {}
    if ids:
        async with get_async_db_context(db) as session:
            rows = await session.execute(
                select(ModelCall.id, Generation.user_id, Connection.owner_id)
                .join(Generation, Generation.id == ModelCall.generation_id)
                .outerjoin(Connection, Connection.id == ModelCall.connection_id)
                .where(ModelCall.id.in_(ids))
            )
            ownership = {cid: (actor, config_owner) for cid, actor, config_owner in rows}
    for message in messages:
        author = message.get('user_id') or owner_id
        calls = message.get('oi_native') or []
        kept, all_owned = [], viewer_id is not None and viewer_id == author
        for native in calls:
            if not isinstance(native, dict):
                all_owned = False
                continue
            actor, config_owner = ownership.get(
                native.get('call_id'),
                (native.get('caller_id') or author, native.get('configuration_owner_id') or author),
            )
            # A removed connection cannot make another caller its configuration owner.
            config_owner = native.get('configuration_owner_id') or config_owner
            owned = viewer_id is not None and viewer_id == actor == config_owner
            all_owned = all_owned and owned
            if portable or native.get('replay_projection'):
                safe = replay_envelope(native)
                if safe:
                    kept.append(safe)
            elif owned:
                kept.append(copy.deepcopy(native))
        if kept:
            message['oi_native'] = kept
        else:
            message.pop('oi_native', None)
        if portable or not all_owned:
            for key in ('oi_native_state', 'previous_response_id', 'response_id'):
                message.pop(key, None)
        if viewer_id is None or viewer_id != author:
            message.pop('meta', None)
    return body
