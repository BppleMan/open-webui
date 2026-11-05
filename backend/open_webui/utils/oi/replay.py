"""Reconnect canonical tool results to matching, unedited native assistant blocks."""

import copy
import hashlib
import json


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _request_projection(message):
    return {
        key: message[key]
        for key in (
            'role',
            'content',
            'name',
            'tool_calls',
            'tool_call_id',
            'function_call',
            'reasoning_content',
            'reasoning',
            'thinking',
            'reasoning_details',
            'reasoning_items',
            'provider_specific_fields',
            'refusal',
            'audio',
            'images',
            'annotations',
            'files',
            'output',
        )
        if key in message
    }


def snapshot_request_native(form):
    """Record request-only replay content before an external filter runs."""
    snapshots = {}
    for message in form.get('messages', []) if isinstance(form, dict) else []:
        if message.get('oi_native'):
            snapshots.setdefault(_digest(message['oi_native']), set()).add(_digest(_request_projection(message)))
    return snapshots


def reconcile_request_native(form, snapshots):
    for message in form.get('messages', []) if isinstance(form, dict) else []:
        native = message.get('oi_native')
        if native and _digest(_request_projection(message)) not in snapshots.get(_digest(native), set()):
            invalidate_native(message)
            message.pop('oi_native', None)
            message.pop('oi_native_state', None)
    return form


def project_native_tools(native, canonical_tools, known_tools=None):
    """Keep only canonical tool calls in a detached outbound native projection.

    Cancellation/pair cleanup is authoritative. Keep retained signed blocks
    untouched, and never mutate the stored provider response.
    """
    native = copy.deepcopy(native)
    if not isinstance(native, dict) or not isinstance(native.get('body'), dict):
        return native
    body, protocol = native['body'], native['protocol']
    canonical_tools = canonical_tools or []
    allowed = {tool['id']: tool for tool in canonical_tools}

    def identity(call, call_id=None, used=(), unique=True):
        if call_id:
            return call_id if call_id in allowed and call_id not in used else None
        name = call.get('name')
        arguments = call.get('args', call.get('arguments', {}))
        if isinstance(arguments, str):
            arguments = json.loads(arguments or '{}')
        matches = [
            tool['id']
            for tool in canonical_tools
            if tool['id'] not in used
            and tool['function']['name'] == name
            and json.loads(tool['function'].get('arguments') or '{}') == arguments
        ]
        if len(matches) > 1 and unique:
            raise ValueError('Ambiguous native tool identity; cannot safely replay this history')
        return matches[0] if matches else None

    if protocol == 'anthropic':
        body['content'] = [b for b in body.get('content', []) if b.get('type') != 'tool_use' or b.get('id') in allowed]
    elif protocol in {'gemini', 'cpa-agy'}:
        candidate = (body.get('candidates') or [{}])[0]
        parts = candidate.get('content', {}).get('parts', [])
        ids = {int(k): v for k, v in (native.get('generated_tool_ids') or {}).items()}
        function_parts = [(index, part['functionCall']) for index, part in enumerate(parts) if part.get('functionCall')]
        ordered = canonical_tools if known_tools is None else known_tools
        # Legacy responses have no ID sidecar. The saved output order includes
        # cancelled calls too, so identical parallel functions remain distinguishable.
        if len(function_parts) == len(ordered) and all(
            call['name'] == tool['function']['name']
            and call.get('args', {}) == json.loads(tool['function'].get('arguments') or '{}')
            for (_, call), tool in zip(function_parts, ordered)
        ):
            for (index, call), tool in zip(function_parts, ordered):
                if not call.get('id'):
                    ids.setdefault(index, tool['id'])
        kept, remapped, seen = [], {}, set()
        for index, part in enumerate(parts):
            call = part.get('functionCall')
            if call:
                tool_id = identity(call, ids.get(index) or call.get('id'))
                if not tool_id:
                    continue
                if tool_id in seen:
                    raise ValueError('Ambiguous native tool identity; cannot safely replay this history')
                seen.add(tool_id)
                if index in ids or not call.get('id'):
                    remapped[len(kept)] = tool_id
            kept.append(part)
        if 'content' in candidate:
            candidate['content']['parts'] = kept
        native['generated_tool_ids'] = remapped
    elif protocol == 'gemini-interactions':
        for key in ('steps', 'outputs'):
            if key in body:
                body[key] = [b for b in body[key] if b.get('type') != 'function_call' or b.get('id') in allowed]
    elif protocol == 'openai-responses':
        body['output'] = [
            b
            for b in body.get('output', [])
            if b.get('type') != 'function_call' or (b.get('call_id') or b.get('id')) in allowed
        ]
    elif protocol in {'openai-chat', 'ollama'}:
        message = body.get('message') if protocol == 'ollama' else (body.get('choices') or [{}])[0].get('message')
        if message and 'tool_calls' in message:
            kept, used, remapped = [], set(), {}
            ids = (
                {int(k): v for k, v in (native.get('generated_tool_ids') or {}).items()} if protocol == 'ollama' else {}
            )
            for index, tool in enumerate(message['tool_calls']):
                # Ollama has no native call IDs; consume identical functions in
                # order, once per surviving canonical result.
                tool_id = identity(tool.get('function', {}), ids.get(index) or tool.get('id'), used, unique=False)
                if tool_id:
                    if protocol == 'ollama' and (index in ids or not tool.get('id')):
                        remapped[len(kept)] = tool_id
                    kept.append(tool)
                    used.add(tool_id)
            message['tool_calls'] = kept
            if protocol == 'ollama':
                native['generated_tool_ids'] = remapped
    return native


def native_function_slots(native):
    """Positions refer to the saved raw block order, not provider stream indexes."""
    from open_webui.utils.misc import get_provider_function

    if native['protocol'] == 'ollama':
        return [
            (index, call.get('id'), get_provider_function(call.get('function', {})))
            for index, call in enumerate(native['body'].get('message', {}).get('tool_calls') or [])
        ]
    if native['protocol'] in {'gemini', 'cpa-agy'}:
        blocks = (native['body'].get('candidates') or [{}])[0].get('content', {}).get('parts', [])
        return [
            (
                index,
                block['functionCall'].get('id'),
                get_provider_function(
                    {'name': block['functionCall']['name'], 'arguments': block['functionCall'].get('args', {})}
                ),
            )
            for index, block in enumerate(blocks)
            if block.get('functionCall')
        ]
    return []


def without_undispatched_tools(native):
    """A retained provider block is not proof that it reached the executor."""
    from open_webui.utils.oi.protocols import NativeStream

    if native.get('protocol') in {'gemini', 'cpa-agy'}:
        state = NativeStream(native['protocol'])
        state.body = native['body']
        if not native.get('replayable', True) or state.ending(True)['normalized'] != 'tool_calls':
            return project_native_tools(native, [])
    return native


def remap_stream_tool(request, response, index, original_id, canonical_id):
    """Apply a consumer-assigned ID only to the exact server-owned live call.

    The registry holds the adapter state until its iterator's finally block saves
    it. Thus immediate replay, persistence and temporary capsules share one map.
    """
    call_id = response.headers.get('x-oi-native-call-id')
    state = getattr(request.state, 'oi_native_streams', {}).get(call_id)
    if state is not None:
        state.remap_tool_id(index, original_id, canonical_id)


def prepare_native_calls(saved):
    from open_webui.utils.misc import get_provider_function

    calls = saved.get('oi_native') or []
    if not all(native.get('protocol') in {'gemini', 'cpa-agy', 'ollama'} for native in calls):
        return calls
    known_tools = [
        {'id': item['call_id'], 'function': get_provider_function(item)}
        for item in saved.get('output', [])
        if item.get('type') == 'function_call'
    ]
    if not known_tools and not isinstance(saved.get('output'), list):
        known_tools = [
            {'id': tool['id'], 'function': get_provider_function(tool.get('provider_function') or tool['function'])}
            for tool in saved.get('tool_calls') or []
        ]
    if not known_tools:
        return [without_undispatched_tools(native) for native in calls]
    # Older/imported output may precede the first native call. Only the native
    # suffix participates in positional recovery; undispatched raw blocks do not.
    calls = [without_undispatched_tools(native) for native in calls]
    offset = len(known_tools) - sum(len(native_function_slots(native)) for native in calls)
    if offset < 0:
        raise ValueError('Native tool does not match the saved output')
    prepared = []
    for native in calls:
        slots = native_function_slots(native)
        count = len(slots)
        segment = known_tools[offset : offset + count]
        pending = list(segment)
        ids = {int(k): v for k, v in (native.get('generated_tool_ids') or {}).items()}
        for index, provider_id, function in slots:
            tool_id = ids.get(index) or provider_id
            match = next(
                (
                    tool
                    for tool in pending
                    if (not tool_id or tool['id'] == tool_id)
                    and tool['function']['name'] == function['name']
                    and json.loads(tool['function']['arguments']) == json.loads(function['arguments'])
                ),
                None,
            )
            if match is None:
                raise ValueError('Native tool does not match the saved output')
            pending.remove(match)
        prepared.append(project_native_tools(native, segment, segment))
        offset += count
    return prepared


def replay_message(native, expected_tools=None):
    """Decode stored output with stable canonical IDs, without changing signed parts.

    Older envelopes lack the ID sidecar. Recover those IDs only from the same
    unedited canonical tool sequence after matching the function and arguments.
    This projects history; normal live decoding still enforces terminal status.
    """
    from open_webui.utils.oi.protocols import NativeStream

    tool_ids = dict(native.get('generated_tool_ids') or {})
    if native['protocol'] in {'gemini', 'cpa-agy', 'ollama'}:
        canonical = iter(expected_tools or [])
        for index, provider_id, function in native_function_slots(native):
            expected = next(canonical, None)
            if provider_id or index in tool_ids or str(index) in tool_ids:
                continue
            if (
                expected is None
                or expected['function']['name'] != function['name']
                or json.loads(expected['function'].get('arguments') or '{}') != json.loads(function['arguments'])
            ):
                raise ValueError('Native tool does not match the saved output')
            tool_ids[index] = expected['id']
    return NativeStream(native['protocol'], generated_tool_ids=tool_ids).nonstream(copy.deepcopy(native['body']))[
        'choices'
    ][0]['message']


def native_messages(saved):
    """A media-only native turn is nonempty even without canonical text items."""
    if saved.get('oi_native_invalidated') or saved.get('role') != 'assistant':
        return []
    from open_webui.utils.oi.native_privacy import replay_envelope

    messages = []
    for native in prepare_native_calls(saved):
        if not isinstance(native, dict) or not native.get('binding_id') or not native.get('replayable', True):
            continue
        safe = replay_envelope(native)
        if not safe or not any(safe['body'].values()):
            continue
        projected = replay_message(native)
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
    calls = prepare_native_calls(saved)
    assistants = [m for m in messages if m.get('role') == 'assistant']
    if not calls:
        return
    if len(calls) != len(assistants) or (
        saved.get('output') and all(native.get('protocol') in {'gemini', 'cpa-agy', 'ollama'} for native in calls)
    ):
        # Continue can merge call outputs; imported history can lack envelopes.
        # Match from the newest call backwards, retaining any canonical prefix.
        from open_webui.utils.oi.protocols import content_text

        pending, rebuilt = list(calls), []
        for message in reversed(messages):
            if message.get('role') != 'assistant' or not pending:
                rebuilt.insert(0, message)
                continue
            remaining_text = content_text(message.get('content'))
            remaining_tools = list(message.get('tool_calls') or [])
            group = []
            while pending:
                native = project_native_tools(pending[-1], remaining_tools)
                projected = replay_message(native, remaining_tools)
                has_output = any(projected.get(key) for key in ('content', 'tool_calls', 'reasoning_content', 'images'))
                # Respect the canonical converter's selected reasoning format.
                # Rebuilding call boundaries must not add a higher-priority alias
                # that later masks an explicit filter edit.
                reasoning = projected.pop('reasoning_content', None)
                if reasoning is not None:
                    for field in ('thinking', 'reasoning_content', 'reasoning'):
                        if field in message:
                            projected[field] = reasoning
                            break
                tool_ids = [t['id'] for t in projected.get('tool_calls', [])]
                count = len(tool_ids)
                if count and tool_ids != [t['id'] for t in remaining_tools[-count:]]:
                    raise ValueError('Native tool does not match the saved output')
                text = ''.join(content_text(projected.get('content')).split())
                positions = [i for i, char in enumerate(remaining_text) if not char.isspace()]
                expected = ''.join(remaining_text[i] for i in positions)
                if not expected.endswith(text):
                    raise ValueError('Native continuation does not match the saved output')
                if text:
                    remaining_text = remaining_text[: positions[-len(text)]].rstrip()
                if count:
                    remaining_tools = remaining_tools[:-count]
                pending.pop()
                if has_output:
                    group.insert(
                        0,
                        {
                            **projected,
                            **({'oi_native': copy.deepcopy(native)} if native.get('replayable', True) else {}),
                        },
                    )
                if not remaining_text.strip() and not remaining_tools:
                    # Reasoning-only calls can precede the text in this same
                    # canonical assistant. Do not strand their signed blocks.
                    if not pending:
                        break
                    preceding = replay_message(project_native_tools(pending[-1], []))
                    if content_text(preceding.get('content')).strip() or native_function_slots(pending[-1]):
                        break
            if remaining_text.strip() or remaining_tools:
                prefix = {**message, 'content': remaining_text}
                prefix.pop('oi_native', None)
                if remaining_tools:
                    prefix['tool_calls'] = remaining_tools
                else:
                    prefix.pop('tool_calls', None)
                group.insert(0, prefix)
            rebuilt[0:0] = group
        if pending:
            raise ValueError('Unmatched native continuation calls')
        messages[:] = rebuilt
        return
    for message, native in zip(assistants, calls):
        # The display model can be a preset (including a chain of presets).
        # The outbound adapter checks the actual binding and protocol instead.
        if isinstance(native, dict) and native.get('binding_id') and isinstance(native.get('body'), dict):
            message['oi_native'] = project_native_tools(native, message.get('tool_calls'))


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
