"""Reconnect canonical tool results to matching, unedited native assistant blocks."""

import copy


def native_messages(saved):
    """A media-only native turn is nonempty even without canonical text items."""
    if saved.get('oi_native_invalidated') or saved.get('role') != 'assistant':
        return []
    from open_webui.utils.oi.protocols import NativeStream
    from open_webui.utils.oi.native_privacy import replay_envelope

    messages = []
    for native in saved.get('oi_native') or []:
        if not isinstance(native, dict) or not native.get('binding_id') or not native.get('replayable', True):
            continue
        safe = replay_envelope(native)
        if not safe or not any(safe['body'].values()):
            continue
        projected = NativeStream(native['protocol']).nonstream(copy.deepcopy(native['body']))['choices'][0]['message']
        messages.append({**projected, 'oi_native': copy.deepcopy(native)})
    return messages


def replace_output_text(output, text):
    """Replace prose while retaining reasoning, media and tool/result blocks."""
    output = copy.deepcopy(output or [])
    text_items = [item for item in output if item.get('type') == 'message']
    for item in text_items:
        item['content'] = [part for part in item.get('content', []) if part.get('type') not in {'text', 'output_text'}]
    if text_items:
        text_items[-1]['content'].append({'type': 'output_text', 'text': text})
    else:
        output.append({'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]})
    return output


def attach_native(saved, messages):
    if saved.get('oi_native_invalidated'):
        return
    calls = saved.get('oi_native') or []
    assistants = [m for m in messages if m.get('role') == 'assistant']
    if not calls or len(calls) < len(assistants):
        return
    if len(calls) != len(assistants):
        # Continue may merge consecutive assistant outputs into one display message.
        # Recover call boundaries from native projections, checking visible text/tool IDs.
        from open_webui.utils.oi.protocols import NativeStream, content_text

        pending, rebuilt = list(calls), []
        remaining_assistants = len(assistants)
        for message in messages:
            if message.get('role') != 'assistant':
                rebuilt.append(message)
                continue
            remaining_assistants -= 1
            expected = ''.join(content_text(message.get('content')).split())
            expected_tools = {t['id'] for t in message.get('tool_calls', [])}
            text, tools, group = '', set(), []
            while pending:
                native = pending.pop(0)
                projection = NativeStream(native['protocol']).nonstream(copy.deepcopy(native['body']))
                projected = projection['choices'][0]['message']
                text += ''.join(content_text(projected.get('content')).split())
                tools.update(t['id'] for t in projected.get('tool_calls', []))
                group.append({**projected, 'oi_native': copy.deepcopy(native)})
                if text == expected and tools == expected_tools and remaining_assistants:
                    break
            if text != expected or tools != expected_tools:
                raise ValueError('Native continuation does not match the saved output')
            rebuilt.extend(group)
        if pending:
            raise ValueError('Unmatched native continuation calls')
        messages[:] = rebuilt
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
    history = getattr(request.state, 'oi_native_call_history', {})
    history[key] = [c for c in history.get(key, []) if c['call_id'] != envelope['call_id']] + [copy.deepcopy(envelope)]
    request.state.oi_native_call_history = history


def attach_latest_native(request, metadata, messages):
    key = metadata.get('oi_generation_id') or metadata.get('message_id') or 'api'
    native = getattr(request.state, 'oi_native_responses', {}).get(key)
    if native:
        attach_native({'oi_native': [native]}, messages)
