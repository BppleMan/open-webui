"""Reconnect canonical tool results to matching, unedited native assistant blocks."""

import copy


def attach_native(saved, messages):
    if saved.get('oi_native_invalidated'):
        return
    calls = saved.get('oi_native') or []
    assistants = [m for m in messages if m.get('role') == 'assistant']
    if len(calls) != len(assistants):
        # An edited/reduced message must never replay an opaque stale continuation.
        return
    for message, native in zip(assistants, calls):
        # The display model can be a preset (including a chain of presets).
        # The outbound adapter checks the actual binding and protocol instead.
        if isinstance(native, dict) and native.get('binding_id') and isinstance(native.get('body'), dict):
            message['oi_native'] = copy.deepcopy(native)


def invalidate_native(message):
    message['oi_native_invalidated'] = True
    message.pop('previous_response_id', None)
    message.pop('response_id', None)


def reconcile_native_edit(message, previous):
    """An opaque envelope is valid only for the server's unchanged source content."""
    old = previous.get(message.get('id'))
    existing = old
    if old is None and message.get('oi_source_message_id'):
        candidate = previous.get(message['oi_source_message_id'])
        if candidate and candidate.get('role') == 'assistant' and candidate.get('parentId') == message.get('parentId'):
            old = candidate
    if old is None and message.get('oi_native'):
        # Save As Copy gets a new ID. Find its source by the saved call identity,
        # never by display text or model name. Do not trust a client-supplied body.
        call_ids = [(c.get('call_id'), c.get('binding_id')) for c in message['oi_native'] if isinstance(c, dict)]
        old = next(
            (
                m
                for m in previous.values()
                if call_ids
                and call_ids
                == [(c.get('call_id'), c.get('binding_id')) for c in m.get('oi_native', []) if isinstance(c, dict)]
            ),
            None,
        )
    if message.get('role') != 'assistant':
        message.pop('oi_native', None)
        message.pop('oi_source_message_id', None)
        return
    source = existing.get('oi_source_message_id') if existing else old.get('id') if old else None
    if source and source != message.get('id'):
        message['oi_source_message_id'] = source
    else:
        message.pop('oi_source_message_id', None)
    if (
        old
        and not old.get('oi_native_invalidated')
        and all(old.get(key) == message.get(key) for key in ('content', 'output'))
    ):
        if old.get('oi_native'):
            message['oi_native'] = copy.deepcopy(old['oi_native'])
        else:
            message.pop('oi_native', None)
        message.pop('oi_native_invalidated', None)
    elif old or message.get('oi_native'):
        invalidate_native(message)


def remember_native(request, metadata, envelope):
    # A request-local cache also supports temporary chats without persisting text.
    cache = getattr(request.state, 'oi_native_responses', None)
    if cache is None:
        cache = request.state.oi_native_responses = {}
    key = metadata.get('oi_generation_id') or metadata.get('message_id') or 'api'
    cache[key] = copy.deepcopy(envelope)


def attach_latest_native(request, metadata, messages):
    key = metadata.get('oi_generation_id') or metadata.get('message_id') or 'api'
    native = getattr(request.state, 'oi_native_responses', {}).get(key)
    if native:
        attach_native({'oi_native': [native]}, messages)
