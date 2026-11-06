"""Reconnect canonical tool results to matching, unedited native assistant blocks."""

import copy
import hashlib
import json
from collections import Counter


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
    """Prune detached replay blocks using call-scoped canonical identities."""
    native = copy.deepcopy(native)
    if not isinstance(native, dict) or not isinstance(native.get('body'), dict):
        return native
    canonical_tools = canonical_tools or []
    allowed = {tool['id']: tool for tool in canonical_tools}
    if native.get('consumer_invalidated'):
        native['consumer_output'] = [
            item
            for item in native.get('consumer_output', [])
            if item.get('type') != 'function_call' or item.get('call_id') in allowed
        ]
        return native
    ids = {int(k): v for k, v in (native.get('generated_tool_ids') or {}).items()}
    slots = native_function_slots(native)
    ordered = canonical_tools if known_tools is None else known_tools

    def same_function(left, right):
        return left['name'] == right['name'] and json.loads(left.get('arguments') or '{}') == json.loads(
            right.get('arguments') or '{}'
        )

    if len(slots) == len(ordered):
        for (index, provider_id, function), tool in zip(slots, ordered):
            if not provider_id and same_function(function, tool['function']):
                ids.setdefault(index, tool['id'])
    retained = set()
    used = set()
    for index, provider_id, function in slots:
        tool_id = ids.get(index) or provider_id
        if not tool_id:
            matches = [
                tool['id']
                for tool in canonical_tools
                if tool['id'] not in used and same_function(tool['function'], function)
            ]
            if len(matches) > 1 and native['protocol'] != 'ollama':
                raise ValueError('Ambiguous native tool identity; cannot safely replay this history')
            tool_id = matches[0] if matches else None
        if tool_id in allowed and tool_id not in used:
            retained.add(index)
            used.add(tool_id)
            ids[index] = tool_id
    body, protocol = native['body'], native['protocol']
    if protocol in {'openai-chat', 'ollama'}:
        holder = body.get('message') if protocol == 'ollama' else (body.get('choices') or [{}])[0].get('message')
        key = 'tool_calls'
    elif protocol in {'gemini', 'cpa-agy'}:
        holder = (body.get('candidates') or [{}])[0].get('content')
        key = 'parts'
    else:
        holder = body
        key = (
            'content'
            if protocol == 'anthropic'
            else 'output'
            if protocol == 'openai-responses'
            else 'steps'
            if 'steps' in body
            else 'outputs'
        )
    if holder is not None and key in holder:
        tool_positions = {index for index, _, _ in slots}
        kept, remapped = [], {}
        for index, block in enumerate(holder[key]):
            if index in tool_positions and index not in retained:
                continue
            if index in retained:
                remapped[len(kept)] = ids[index]
            kept.append(block)
        holder[key] = kept
        native['generated_tool_ids'] = remapped
    if isinstance(native.get('consumer_output'), list):
        native['consumer_output'] = [
            item
            for item in native['consumer_output']
            if item.get('type') != 'function_call' or item.get('call_id') in allowed
        ]
    return native


def native_function_slots(native):
    """Positions refer to the saved raw block order, not provider stream indexes."""
    from open_webui.utils.misc import get_provider_function

    body, protocol = native['body'], native['protocol']
    if protocol in {'ollama', 'openai-chat'}:
        message = (
            body.get('message', {}) if protocol == 'ollama' else (body.get('choices') or [{}])[0].get('message', {})
        )
        return [
            (index, call.get('id'), get_provider_function(call.get('function', {})))
            for index, call in enumerate(message.get('tool_calls') or [])
        ]
    if protocol == 'anthropic':
        return [
            (
                index,
                block.get('id'),
                get_provider_function({'name': block['name'], 'arguments': block.get('input', {})}),
            )
            for index, block in enumerate(body.get('content', []))
            if block.get('type') == 'tool_use'
        ]
    if protocol in {'openai-responses', 'gemini-interactions'}:
        blocks = (
            body.get('output', []) if protocol == 'openai-responses' else body.get('steps', body.get('outputs', []))
        )
        return [
            (index, block.get('call_id') or block.get('id'), get_provider_function(block))
            for index, block in enumerate(blocks)
            if block.get('type') == 'function_call'
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
    # A changed stream uses its recorded canonical projection, not raw provider
    # identities. Consumer deduplication must not remap that invalidated envelope.
    if state is not None and not state.replay_invalidated:
        state.remap_tool_id(index, original_id, canonical_id)


def stream_event_projection(event):
    """Metering/status-only filters do not invalidate assistant replay."""
    if not isinstance(event, dict):
        return None
    if 'choices' in event:
        return [
            {key: _request_projection(choice[key]) for key in ('delta', 'message') if isinstance(choice.get(key), dict)}
            for choice in event['choices']
        ]
    if event.get('type', '').startswith('response.'):
        return {
            **{
                key: event[key]
                for key in (
                    'type',
                    'item',
                    'part',
                    'delta',
                    'text',
                    'arguments',
                    'item_id',
                    'output_index',
                    'content_index',
                )
                if key in event
            },
            **({'output': event['response'].get('output')} if isinstance(event.get('response'), dict) else {}),
        }
    return None


def invalidate_stream_native(request, response):
    state = getattr(request.state, 'oi_native_streams', {}).get(response.headers.get('x-oi-native-call-id'))
    if state is not None:
        state.replay_invalidated = True


REPLAYABLE_TOOL_STATUSES = {'completed', 'failed', 'rejected', 'pending', 'queued', 'requires_approval'}


def consumer_atoms(output, *, excluded_tools=(), execution=False):
    """Ignore lifecycle metadata; retain generated text, reasoning and functions.

    Continue may join message items, but retains their ordered content parts.
    Tool execution adds results and may rename a wrapped function while keeping
    its provider_function; neither changes the provider's generated projection.
    """
    from open_webui.utils.misc import get_provider_function

    atoms = []
    for item in output or []:
        kind = item.get('type')
        if kind == 'message':
            for part in item.get('content', []):
                if part.get('text') == '' and part.get('type') in {'text', 'output_text'}:
                    continue
                atoms.append(['message', item.get('role', 'assistant'), part])
        elif kind == 'reasoning':
            atoms.append(
                [
                    'reasoning',
                    {
                        key: item[key]
                        for key in ('content', 'summary', 'start_tag', 'end_tag', 'attributes', 'reasoning_details')
                        if key in item
                    },
                ]
            )
        elif kind == 'function_call':
            if item.get('call_id') in excluded_tools or item.get('status') not in REPLAYABLE_TOOL_STATUSES:
                continue
            function = get_provider_function(
                {k: v for k, v in item.items() if k != 'provider_function'} if execution else item
            )
            atoms.append(
                ['function', item.get('call_id'), function['name'], json.loads(function.get('arguments') or '{}')]
            )
        elif kind == 'open_webui:code_interpreter':
            atoms.append(['code', item.get('code'), item.get('language')])
    return atoms


def validate_consumer_projections(saved, calls):
    if not isinstance(saved.get('output'), list):
        return  # The immediate continuation is already server-owned.
    excluded = {
        item.get('call_id')
        for item in saved['output']
        if item.get('type') == 'function_call' and item.get('status') not in REPLAYABLE_TOOL_STATUSES
    }
    actual = consumer_atoms(saved['output'], excluded_tools=excluded)
    offset = 0
    for native in calls:
        if not isinstance(native.get('consumer_output'), list):
            continue
        expected = consumer_atoms(native['consumer_output'], excluded_tools=excluded)
        if not expected:
            continue
        match = next(
            (i for i in range(offset, len(actual) - len(expected) + 1) if actual[i : i + len(expected)] == expected),
            None,
        )
        if match is None:
            raise ValueError('Native continuation does not match the saved output')
        offset = match + len(expected)
        if native.get('consumer_dispatched'):
            actual_execution = consumer_atoms(saved['output'], excluded_tools=excluded, execution=True)
            expected_execution = consumer_atoms(native['consumer_output'], excluded_tools=excluded, execution=True)
            if actual_execution[match:offset] != expected_execution:
                raise ValueError('Native tool execution does not match the saved output')


async def record_consumer_output(request, metadata, response, output, *, dispatched=False, invalidate=False):
    """Bind the actual consumer result after filters, tags and media conversion.

    The native iterator has finished saving its raw response at this point. Update
    the same call in request cache and persistent history before tools or capsule
    generation can consume it. Temporary content remains request-local.
    """
    from open_webui.models.chats import Chats
    from open_webui.utils.chat_id import is_saved_chat_id

    call_id = response.headers.get('x-oi-native-call-id')
    key = metadata.get('oi_generation_id') or metadata.get('message_id') or 'api'
    native = next(
        (c for c in getattr(request.state, 'oi_native_call_history', {}).get(key, []) if c['call_id'] == call_id), None
    )
    if native is None:
        return
    native = {**native, 'consumer_output': copy.deepcopy(output)}
    if dispatched:
        native['consumer_dispatched'] = True
    if invalidate:
        native.update(replayable=False, consumer_invalidated=True)
    remember_native(request, metadata, native)
    chat_id, message_id = metadata.get('chat_id'), metadata.get('message_id')
    if not metadata.get('task') and is_saved_chat_id(chat_id) and message_id:
        previous = await Chats.get_message_by_id_and_message_id(chat_id, message_id) or {}
        calls = [{**c, **native} if c.get('call_id') == call_id else c for c in previous.get('oi_native', [])]
        await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, {'oi_native': calls}, touch=False)


def provider_tool_results(messages, protocol, binding_id):
    """Translate each result using its preceding call, leaving signed blocks intact."""
    messages = copy.deepcopy(messages)
    ids = {}
    for message in messages:
        native = message.get('oi_native') or {}
        if (
            isinstance(native, dict)
            and native.get('protocol') == protocol
            and native.get('binding_id') == binding_id
            and native.get('replayable', True)
        ):
            mapping = {int(k): v for k, v in (native.get('generated_tool_ids') or {}).items()}
            for index, provider_id, _ in native_function_slots(native):
                if provider_id and index in mapping:
                    ids[mapping[index]] = provider_id
        if message.get('role') == 'tool':
            message['tool_call_id'] = ids.get(message.get('tool_call_id'), message.get('tool_call_id'))
    return messages


def prepare_native_calls(saved):
    from open_webui.utils.misc import get_provider_function

    calls = saved.get('oi_native') or []
    validate_consumer_projections(saved, calls)
    if calls and all('consumer_output' in native for native in calls):
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
        if 'consumer_output' in native:
            prepared.append(native)
            offset += len(native_function_slots(native))
            continue
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

    if isinstance(native.get('consumer_output'), list):
        from open_webui.utils.misc import convert_output_to_messages, get_provider_function

        messages = convert_output_to_messages(native['consumer_output'], raw=True, reasoning_format='reasoning_content')
        projected = next((m for m in messages if m.get('role') == 'assistant'), {'role': 'assistant', 'content': ''})
        tools = [
            {'id': item['call_id'], 'type': 'function', 'function': get_provider_function(item)}
            for item in native['consumer_output']
            if item.get('type') == 'function_call'
            and item.get('call_id')
            and item.get('status') in REPLAYABLE_TOOL_STATUSES
        ]
        if tools:
            projected['tool_calls'] = tools
        if native.get('replayable', True):
            raw = NativeStream(native['protocol'], generated_tool_ids=native.get('generated_tool_ids')).nonstream(
                copy.deepcopy(native['body'])
            )['choices'][0]['message']
            for key in ('images', 'audio', 'refusal', 'annotations'):
                if key in raw:
                    projected[key] = raw[key]
        return projected
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


def match_native_text(projected, saved_output, expected):
    """Accept only a tag projection witnessed by the saved canonical output.

    The live parser is chunk-sensitive: a later opening tag can be consumed in
    an earlier chunk. Try whole and tag-boundary chunks, then recorded block
    boundaries. Every accepted result must preserve both visible text and the
    exact parsed reasoning/attributes. Provider bytes remain untouched.
    """
    import re
    from open_webui.utils.oi.protocols import content_text
    from open_webui.utils.misc import convert_output_to_messages
    from open_webui.utils.output_tags import (
        DEFAULT_REASONING_TAGS,
        DEFAULT_SOLUTION_TAGS,
        create_tag_output_handler,
        _start_tag_pattern,
    )

    def compact_text(value):
        return ''.join(content_text(value).split())

    raw = content_text(projected.get('content'))
    if expected.endswith(compact_text(raw)):
        return projected

    def identity(item):
        return (
            item.get('type'),
            item.get('start_tag'),
            item.get('end_tag'),
            # Solution text can merge with a later Continue. It is checked
            # against the remaining visible text, rather than this metadata.
            compact_text(item.get('content') or item.get('summary')) if item['type'] == 'reasoning' else '',
            json.dumps(item.get('attributes') or {}, sort_keys=True),
        )

    witnesses = [item for item in saved_output or [] if item.get('start_tag') and item.get('end_tag')]
    allowed = Counter(identity(item) for item in witnesses)
    tags = {'reasoning': list(DEFAULT_REASONING_TAGS), 'solution': list(DEFAULT_SOLUTION_TAGS)}
    for item in witnesses:
        kind = 'reasoning' if item['type'] == 'reasoning' else 'solution' if item['type'] == 'message' else None
        pair = (item['start_tag'], item['end_tag'])
        if kind and pair not in tags[kind]:
            tags[kind].append(pair)

    def variants():
        yield [raw]
        matches = [
            (kind, start, end, match)
            for kind, pairs in tags.items()
            for start, end in pairs
            for match in re.finditer(_start_tag_pattern(start), raw)
        ]

        def split_at(points):
            edges = sorted({0, len(raw), *points})
            return [raw[left:right] for left, right in zip(edges, edges[1:])]

        yield split_at(match.start() for _, _, _, match in matches)
        boundaries = []
        for kind, start, end, match in matches:
            close = raw.find(end, match.end())
            inner = raw[match.end() : close if close >= 0 else len(raw)]
            inner = re.sub(_start_tag_pattern(start), '', inner)
            if any(
                key[0] == ('reasoning' if kind == 'reasoning' else 'message')
                and key[1:3] == (start, end)
                and (kind != 'reasoning' or key[3] == compact_text(inner))
                for key in allowed
            ):
                boundaries.append(match.start())
        yield split_at(boundaries)

    for chunks in variants():
        parse, output = create_tag_output_handler(), []
        for chunk in chunks:
            if not output or output[-1]['type'] not in {'message', 'reasoning'}:
                output.append({'type': 'message', 'content': [{'type': 'output_text', 'text': ''}]})
            item = output[-1]
            parts = item.setdefault('content', [])
            if not parts:
                parts.append({'type': 'output_text', 'text': ''})
            parts[-1]['text'] += chunk
            for kind, pairs in tags.items():
                parse(kind, pairs, output)
        observed = Counter(identity(item) for item in output if item.get('start_tag') and item.get('end_tag'))
        if observed - allowed:
            continue
        messages = convert_output_to_messages(output, raw=True, reasoning_format='reasoning_content')
        content = '\n'.join(content_text(message.get('content')) for message in messages)
        if expected.endswith(compact_text(content)):
            result = {**projected, 'content': content}
            reasoning = '\n'.join(
                message['reasoning_content'] for message in messages if message.get('reasoning_content')
            )
            if reasoning:
                result['reasoning_content'] = '\n'.join(filter(None, [result.get('reasoning_content'), reasoning]))
            return result
    raise ValueError('Native continuation does not match the saved output')


def attach_native(saved, messages):
    if saved.get('oi_native_invalidated'):
        return
    calls = prepare_native_calls(saved)
    assistants = [m for m in messages if m.get('role') == 'assistant']
    # Canonical cancellation is authoritative before identifying call boundaries.
    surviving_tools = [tool for message in assistants for tool in message.get('tool_calls', [])]
    calls = [project_native_tools(native, surviving_tools) for native in calls]
    if not calls:
        return
    if (
        any('consumer_output' in native for native in calls)
        or len(calls) != len(assistants)
        or (saved.get('output') and all(native.get('protocol') in {'gemini', 'cpa-agy', 'ollama'} for native in calls))
    ):
        # Continue can merge call outputs; imported history can lack envelopes.
        # Match from the newest call backwards, retaining any canonical prefix.
        from open_webui.utils.oi.protocols import content_text, _contains_opaque_native

        pending, rebuilt = list(calls), []
        for message in reversed(messages):
            if message.get('role') in {'tool', 'user'}:
                # The converter may omit a reasoning/opaque-only assistant after
                # a tool result (including its flattened user image message).
                # Consume those calls on THIS side of that boundary before
                # matching the preceding tool assistant.
                tail = []
                while pending:
                    native = pending[-1]
                    projected = replay_message(native, surviving_tools)
                    try:
                        projected = match_native_text(projected, saved.get('output'), '')
                    except ValueError:
                        break
                    if projected.get('tool_calls'):
                        break
                    pending.pop()
                    has_output = any(
                        projected.get(key) for key in ('content', 'reasoning_content', 'images', 'audio', 'refusal')
                    ) or (native.get('replayable', True) and _contains_opaque_native(native['body']))
                    # A non-default reasoning format already supplied its own
                    # assistant slot. Here only the invisible native slot is missing.
                    projected.pop('reasoning_content', None)
                    if has_output:
                        tail.insert(
                            0,
                            {
                                **projected,
                                **({'oi_native': copy.deepcopy(native)} if native.get('replayable', True) else {}),
                            },
                        )
                rebuilt[0:0] = tail
            if message.get('role') != 'assistant' or not pending:
                rebuilt.insert(0, message)
                continue
            remaining_text = content_text(message.get('content'))
            remaining_tools = list(message.get('tool_calls') or [])
            group = []
            while pending:
                native = project_native_tools(pending[-1], remaining_tools)
                projected = replay_message(native, remaining_tools)
                positions = [i for i, char in enumerate(remaining_text) if not char.isspace()]
                expected = ''.join(remaining_text[i] for i in positions)
                projected = match_native_text(projected, saved.get('output'), expected)
                has_output = any(
                    projected.get(key)
                    for key in ('content', 'tool_calls', 'reasoning_content', 'images', 'audio', 'refusal')
                ) or (native.get('replayable', True) and _contains_opaque_native(native['body']))
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
                    if native_function_slots(pending[-1]):
                        break
                    preceding = replay_message(pending[-1])
                    try:
                        preceding = match_native_text(preceding, saved.get('output'), '')
                    except ValueError:
                        break
                    if content_text(preceding.get('content')).strip():
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
