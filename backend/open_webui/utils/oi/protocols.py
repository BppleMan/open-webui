"""Native protocol adapters. Unknown native response fields stay in the native envelope.

UI metadata never enters provider payloads. No implicit history truncation, retry,
prompt injection, or provider-side conversation storage is enabled here.
"""

import copy
import json
import re
from uuid import uuid4

import httpx
from fastapi import HTTPException
from open_webui.models.chats import Chats
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.oi import accounting, catalog
from open_webui.utils.oi.runtime import SSEDecoder
from open_webui.utils.oi.endpoints import resolve_endpoint
from open_webui.utils.oi.selection import snapshot_target
from open_webui.utils.oi.protocol_registry import canonical_protocol
from starlette.responses import JSONResponse, StreamingResponse


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else copy.deepcopy(value)
        )
    return result


def parts(content):
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}]
    return copy.deepcopy(content or [])


def content_text(content):
    return (
        content
        if isinstance(content, str)
        else ''.join(
            p.get('text', '') for p in (content or []) if p.get('type') in {'text', 'output_text', 'input_text'}
        )
    )


def image_source(url):
    match = re.fullmatch(r'data:([^;,]+);base64,(.*)', url, re.S)
    return {'type': 'base64', 'media_type': match[1], 'data': match[2]} if match else {'type': 'url', 'url': url}


def media_part(part, protocol):
    kind = part.get('type')
    if kind in {'text', 'input_text', 'output_text'}:
        return (
            {'text': part.get('text', '')} if protocol == 'gemini' else {'type': 'text', 'text': part.get('text', '')}
        )
    if kind == 'image_url':
        source = image_source(part['image_url']['url'])
        if protocol == 'anthropic':
            return {'type': 'image', 'source': source}
        if source['type'] == 'base64':
            return {'inlineData': {'mimeType': source['media_type'], 'data': source['data']}}
        return {'fileData': {'fileUri': source['url'], 'mimeType': part.get('mime_type', 'image/jpeg')}}
    if kind == 'input_audio':
        value = part['input_audio']
        if protocol == 'gemini':
            return {'inlineData': {'mimeType': 'audio/' + value['format'], 'data': value['data']}}
        raise HTTPException(422, 'This Anthropic adapter does not accept audio input; choose a compatible connection')
    if kind in {'file', 'input_file'}:
        value = part.get('file', part)
        source = image_source(value.get('file_data', ''))
        if source['type'] != 'base64':
            raise HTTPException(422, 'Native document input requires a data URL or a provider-native block')
        return (
            {'type': 'document', 'source': source}
            if protocol == 'anthropic'
            else {'inlineData': {'mimeType': source['media_type'], 'data': source['data']}}
        )
    # Explicit native content blocks are retained. A wrong protocol fails at validation, not by silently dropping data.
    return copy.deepcopy(part)


def split_system(messages):
    system, conversation, started = [], [], False
    for message in messages:
        if message.get('role') in {'system', 'developer'}:
            if started:
                raise HTTPException(
                    422,
                    'This native protocol cannot preserve a mid-conversation system message; keep instructions at the beginning',
                )
            system.extend(parts(message.get('content', '')))
        else:
            started = True
            conversation.append(message)
    return system, conversation


def native_for(message, protocol, binding_id):
    native = message.get('oi_native') or {}
    return (
        copy.deepcopy(native.get('body'))
        if isinstance(native, dict)
        and not message.get('oi_native_invalidated')
        and native.get('replayable', True)
        and canonical_protocol(native.get('protocol')) == canonical_protocol(protocol)
        and native.get('binding_id') == binding_id
        else None
    )


def clean_message(message):
    return {
        k: copy.deepcopy(v)
        for k, v in message.items()
        if k
        in {
            'role',
            'content',
            'name',
            'tool_calls',
            'tool_call_id',
            'function_call',
            'refusal',
            'audio',
            'annotations',
            'reasoning_content',
            'reasoning_details',
            'provider_specific_fields',
        }
    }


def response_items(messages, binding):
    output = []
    for message in messages:
        native = native_for(message, 'openai-responses', binding.id)
        if native:
            output.extend(native.get('output', []))
            continue
        role = message.get('role')
        if role == 'tool':
            output.append(
                {
                    'type': 'function_call_output',
                    'call_id': message['tool_call_id'],
                    'output': message.get('content', ''),
                }
            )
            continue
        content = []
        for part in parts(message.get('content')):
            kind = part.get('type')
            if kind in {'text', 'input_text', 'output_text'}:
                content.append(
                    {'type': 'output_text' if role == 'assistant' else 'input_text', 'text': part.get('text', '')}
                )
            elif kind == 'image_url':
                content.append({'type': 'input_image', **part['image_url']})
                content[-1]['image_url'] = content[-1].pop('url')
            elif kind == 'file':
                content.append({'type': 'input_file', **part['file']})
            else:
                content.append(part)
        if content:
            output.append({'type': 'message', 'role': role, 'content': content})
        for tool in message.get('tool_calls') or []:
            output.append({'type': 'function_call', 'call_id': tool['id'], **tool['function']})
    return output


def _contains_opaque_native(value):
    if isinstance(value, list):
        return any(_contains_opaque_native(item) for item in value)
    if isinstance(value, dict):
        if any(value.get(key) for key in ('signature', 'thoughtSignature', 'encrypted_content')):
            return True
        if value.get('type') in {'redacted_thinking', 'server_tool_use', 'computer_use', 'reasoning.encrypted'}:
            return True
        return any(_contains_opaque_native(item) for item in value.values())
    return False


def build_request(form, binding, connection):
    binding, connection = snapshot_target(binding, connection)
    protocol = binding.protocol
    source = copy.deepcopy(form)
    messages = source.pop('messages', [])
    stream = bool(source.get('stream', False))
    # OI orchestration fields are never provider extensions.
    for field in (
        'metadata',
        'params',
        'model',
        'id',
        'chat_id',
        'session_id',
        'features',
        'variables',
        'files',
        'background_tasks',
        'model_item',
        'tool_ids',
        'tool_servers',
        'filter_ids',
        'skill_ids',
        'stream_options',
        'capability_params',
    ):
        source.pop(field, None)
    native_options = source.pop('provider_options', {})
    if not isinstance(native_options, dict):
        raise HTTPException(422, 'provider_options must be an object')
    source = merge(source, native_options)
    source.pop('capability_params', None)
    source['model'] = binding.upstream_id
    source.setdefault('store', False)
    for message in messages:
        native = message.get('oi_native') or {}
        if (
            isinstance(native, dict)
            and native.get('protocol')
            and (
                canonical_protocol(native['protocol']) != canonical_protocol(protocol)
                or native.get('binding_id') != binding.id
            )
        ):
            if _contains_opaque_native(native.get('body')):
                raise HTTPException(
                    422,
                    'This history contains signed native content from another model or protocol; use its original binding to preserve it',
                )
    try:
        url = resolve_endpoint(
            connection,
            protocol,
            model=binding.upstream_id.removeprefix('models/')
            if protocol in {'gemini', 'cpa-agy'}
            else binding.upstream_id,
            stream=stream,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if protocol == 'openai-chat':
        source['messages'] = [
            clean_message(((native_for(m, protocol, binding.id) or {}).get('choices') or [{}])[0].get('message') or m)
            for m in messages
        ]
        if stream:
            source['stream_options'] = {**(form.get('stream_options') or {}), 'include_usage': True}
        return url, source
    if protocol == 'openai-responses':
        source['input'] = response_items(messages, binding)
        source['truncation'] = 'disabled'
        if 'max_tokens' in source:
            source['max_output_tokens'] = source.pop('max_tokens')
        if 'max_completion_tokens' in source:
            source['max_output_tokens'] = source.pop('max_completion_tokens')
        if 'reasoning_effort' in source:
            source['reasoning'] = {**source.get('reasoning', {}), 'effort': source.pop('reasoning_effort')}
        if 'response_format' in source:
            value = source.pop('response_format')
            source['text'] = {
                **source.get('text', {}),
                'format': value.get('json_schema', value) | {'type': value.get('type', 'text')},
            }
        source['tools'] = [
            ({'type': 'function', **t['function']} if t.get('type') == 'function' and 'function' in t else t)
            for t in source.get('tools', [])
        ]
        if isinstance(source.get('tool_choice'), dict) and 'function' in source['tool_choice']:
            source['tool_choice'] = {'type': 'function', 'name': source['tool_choice']['function']['name']}
        source.pop('n', None)
        source.setdefault('include', ['reasoning.encrypted_content'])
        return url, source
    if protocol == 'ollama':
        from open_webui.utils.payload import convert_payload_openai_to_ollama

        source.pop('store', None)
        source['messages'] = [clean_message(m) for m in messages]
        payload = convert_payload_openai_to_ollama(source)
        for key in ('think', 'format', 'keep_alive'):
            if key in source:
                payload[key] = copy.deepcopy(source[key])
        for original, converted in zip(messages, payload['messages']):
            native = native_for(original, protocol, binding.id)
            if native and native.get('message'):
                converted.clear()
                converted.update(copy.deepcopy(native['message']))
        return url, payload

    source.pop('store', None)
    system, conversation = split_system(messages)
    if protocol == 'anthropic':
        output = []
        for m in conversation:
            native = native_for(m, protocol, binding.id)
            if native:
                content = native.get('content', [])
            elif m['role'] == 'tool':
                content = [{'type': 'tool_result', 'tool_use_id': m['tool_call_id'], 'content': m.get('content', '')}]
            else:
                content = [media_part(p, 'anthropic') for p in parts(m.get('content'))]
                for tool in m.get('tool_calls') or []:
                    content.append(
                        {
                            'type': 'tool_use',
                            'id': tool['id'],
                            'name': tool['function']['name'],
                            'input': json.loads(tool['function'].get('arguments') or '{}'),
                        }
                    )
            role = 'user' if m['role'] == 'tool' else m['role']
            if output and output[-1]['role'] == role:
                output[-1]['content'].extend(content)
            else:
                output.append({'role': role, 'content': content})
        source['messages'] = output
        if system:
            source['system'] = [media_part(p, 'anthropic') for p in system]
        if 'max_completion_tokens' in source:
            source['max_tokens'] = source.pop('max_completion_tokens')
        if 'max_tokens' not in source:
            maximum = catalog.capabilities(binding).get('output_limit')
            if maximum is None:
                raise HTTPException(422, 'Claude requires max_tokens; configure an output budget for this model')
            source['max_tokens'] = maximum
        if 'stop' in source:
            value = source.pop('stop')
            source['stop_sequences'] = [value] if isinstance(value, str) else value
        source['tools'] = [
            (
                {
                    'name': t['function']['name'],
                    'description': t['function'].get('description', ''),
                    'input_schema': t['function'].get('parameters', {'type': 'object', 'properties': {}}),
                }
                if t.get('type') == 'function' and 'function' in t
                else t
            )
            for t in source.get('tools', [])
        ]
        choice = source.get('tool_choice')
        if isinstance(choice, str):
            source['tool_choice'] = {'type': 'any' if choice == 'required' else choice}
        elif isinstance(choice, dict) and 'function' in choice:
            source['tool_choice'] = {'type': 'tool', 'name': choice['function']['name']}
        if 'parallel_tool_calls' in source:
            source['tool_choice'] = {
                **source.get('tool_choice', {'type': 'auto'}),
                'disable_parallel_tool_use': not source.pop('parallel_tool_calls'),
            }
        return url, source

    if protocol in {'gemini', 'cpa-agy'}:
        contents, names, provider_ids = [], {}, set()
        for m in conversation:
            for tool in m.get('tool_calls') or []:
                names[tool['id']] = tool['function']['name']
            native = native_for(m, protocol, binding.id)
            if native:
                content = copy.deepcopy(
                    (native.get('candidates') or [{}])[0].get('content') or {'role': 'model', 'parts': []}
                )
                # CPA's private RP contract explicitly excludes historical Gemini thought replay.
                if protocol == 'cpa-agy':
                    content['parts'] = [p for p in content.get('parts', []) if not p.get('thought')]
            else:
                converted = []
                if m['role'] == 'tool':
                    value = m.get('content', '')
                    try:
                        value = json.loads(value) if isinstance(value, str) else value
                    except ValueError:
                        pass
                    converted = [
                        {
                            'functionResponse': {
                                'name': m.get('name') or names.get(m['tool_call_id'], m['tool_call_id']),
                                **({'id': m['tool_call_id']} if m['tool_call_id'] in provider_ids else {}),
                                'response': value if isinstance(value, dict) else {'result': value},
                            }
                        }
                    ]
                else:
                    converted = [media_part(p, 'gemini') for p in parts(m.get('content'))]
                    for t in m.get('tool_calls') or []:
                        converted.append(
                            {
                                'functionCall': {
                                    'id': t['id'],
                                    'name': t['function']['name'],
                                    'args': json.loads(t['function'].get('arguments') or '{}'),
                                }
                            }
                        )
                content = {'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': converted}
            for p in content.get('parts', []):
                if p.get('functionCall'):
                    t = p['functionCall']
                    names[t.get('id', t['name'])] = t['name']
                    if t.get('id'):
                        provider_ids.add(t['id'])
            contents.append(content)
        source.pop('model')
        source.pop('stream', None)
        source['contents'] = contents
        if system:
            source['systemInstruction'] = {'parts': [media_part(p, 'gemini') for p in system]}
        config = source.pop('generationConfig', {})
        if not isinstance(config, dict):
            raise HTTPException(422, 'generationConfig must be an object')
        thinking = config.setdefault('thinkingConfig', {})
        if not isinstance(thinking, dict):
            raise HTTPException(422, 'thinkingConfig must be an object')
        # Preserve explicit model/session choices and all existing effort/budget fields.
        # Summary text arrives in this same stream; no auxiliary generation is needed.
        thinking.setdefault('includeThoughts', True)
        if not isinstance(thinking['includeThoughts'], bool):
            raise HTTPException(422, 'includeThoughts must be a boolean')
        for src, dst in {
            'temperature': 'temperature',
            'top_p': 'topP',
            'top_k': 'topK',
            'max_tokens': 'maxOutputTokens',
            'max_completion_tokens': 'maxOutputTokens',
            'stop': 'stopSequences',
            'seed': 'seed',
            'frequency_penalty': 'frequencyPenalty',
            'presence_penalty': 'presencePenalty',
        }.items():
            if src in source:
                config[dst] = source.pop(src)
        if isinstance(config.get('stopSequences'), str):
            config['stopSequences'] = [config['stopSequences']]
        unsupported = {
            'logit_bias',
            'repetition_penalty',
            'repeat_penalty',
            'min_p',
            'mirostat',
            'mirostat_eta',
            'mirostat_tau',
        } & source.keys()
        if unsupported:
            raise HTTPException(422, 'Unsupported Gemini controls: ' + ', '.join(sorted(unsupported)))
        source['generationConfig'] = config
        declarations, native_tools = [], []
        for tool in source.pop('tools', []):
            if tool.get('type') == 'function' and 'function' in tool:
                declarations.append(tool['function'])
            else:
                native_tools.append(tool)
        if declarations:
            native_tools.append({'functionDeclarations': declarations})
        if native_tools:
            source['tools'] = native_tools
        choice = source.pop('tool_choice', None)
        if choice:
            cfg = (
                {'mode': {'auto': 'AUTO', 'required': 'ANY', 'none': 'NONE'}.get(choice, 'AUTO')}
                if isinstance(choice, str)
                else {'mode': 'ANY', 'allowedFunctionNames': [choice['function']['name']]}
            )
            source['toolConfig'] = {**source.get('toolConfig', {}), 'functionCallingConfig': cfg}
        source.pop('parallel_tool_calls', None)
        return url, source

    if protocol == 'gemini-interactions':
        source['store'] = False
        source['input'] = interaction_input(conversation, binding)
        if any(part.get('type') not in {'text', 'input_text', 'output_text'} for part in system):
            raise HTTPException(
                422, 'Interactions system instructions must be text; native media belongs in conversation content'
            )
        if system:
            source['system_instruction'] = '\n'.join(p.get('text', '') for p in system)
        config = source.pop('generation_config', {})
        for src, dst in {
            'temperature': 'temperature',
            'top_p': 'top_p',
            'top_k': 'top_k',
            'max_tokens': 'max_output_tokens',
            'max_completion_tokens': 'max_output_tokens',
            'stop': 'stop_sequences',
            'seed': 'seed',
            'tool_choice': 'tool_choice',
        }.items():
            if src in source:
                config[dst] = source.pop(src)
        source['generation_config'] = config
        source['tools'] = [
            ({'type': 'function', **t['function']} if 'function' in t else t) for t in source.get('tools', [])
        ]
        return url, source
    raise HTTPException(422, 'Unsupported native protocol')


def interaction_parts(content):
    converted = []
    for part in parts(content):
        kind = part.get('type')
        if kind in {'text', 'input_text', 'output_text'}:
            converted.append({**part, 'type': 'text'})
        elif kind == 'image_url':
            source = image_source(part['image_url']['url'])
            converted.append(
                {
                    'type': 'image',
                    **(
                        {'data': source['data'], 'mime_type': source['media_type']}
                        if source['type'] == 'base64'
                        else {'uri': source['url']}
                    ),
                }
            )
        elif kind == 'input_audio':
            audio = part['input_audio']
            converted.append({'type': 'audio', 'data': audio['data'], 'mime_type': 'audio/' + audio['format']})
        elif kind in {'file', 'input_file'}:
            value = part.get('file', part)
            source = image_source(value.get('file_data', ''))
            if source['type'] != 'base64':
                raise HTTPException(422, 'Interactions document input requires a data URL or a native document block')
            converted.append({'type': 'document', 'data': source['data'], 'mime_type': source['media_type']})
        else:
            converted.append(copy.deepcopy(part))
    return converted


def interaction_input(messages, binding):
    result = []
    for message in messages:
        native = native_for(message, 'gemini-interactions', binding.id)
        if native:
            result.extend(native.get('steps', native.get('outputs', [])))
        elif message['role'] == 'tool':
            result.append(
                {
                    'type': 'function_result',
                    'call_id': message['tool_call_id'],
                    'result': interaction_parts(message.get('content', '')),
                }
            )
        else:
            converted = interaction_parts(message.get('content'))
            if converted:
                result.append(
                    {'type': 'model_output' if message['role'] == 'assistant' else 'user_input', 'content': converted}
                )
            for tool in message.get('tool_calls') or []:
                result.append(
                    {
                        'type': 'function_call',
                        'id': tool['id'],
                        'name': tool['function']['name'],
                        'arguments': json.loads(tool['function'].get('arguments') or '{}'),
                    }
                )
    return result


def chunk(delta=None, usage=None, finish=None, **extra):
    result = {'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}], **extra}
    if usage is not None:
        result['usage'] = usage
    return result


def inline_images(parts):
    images = []
    for part in parts:
        media = part.get('inlineData') or part.get('inline_data') or {}
        mime = media.get('mimeType') or media.get('mime_type') or ''
        if mime.startswith('image/') and isinstance(media.get('data'), str) and media['data']:
            images.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{media["data"]}'}})
    return images


class NativeStream:
    def __init__(self, protocol):
        self.protocol = protocol
        self.body = {}
        self.blocks = {}
        self.usage = {}
        self.finished = False
        self.tool_index = 0
        self.generated_tool_ids = {}
        self.input_media = False

    def metering(self):
        if self.protocol in {'gemini', 'cpa-agy'}:
            return accounting.gemini_metering(self.usage, self.body, input_media=self.input_media)
        return copy.deepcopy(self.usage)

    def _usage(self):
        return accounting.normalize_usage(self.metering(), self.protocol)

    def usage_event(self):
        return chunk(usage=self._usage(), oi_usage=self.metering(), oi_usage_protocol=self.protocol)

    def ending(self, tools):
        if self.protocol == 'anthropic':
            reason = self.body.get('stop_reason')
        elif self.protocol in {'gemini', 'cpa-agy'}:
            reason = (self.body.get('candidates') or [{}])[0].get('finishReason')
            reason = reason or self.body.get('promptFeedback', {}).get('blockReason')
        else:
            reason = self.body.get('status')
        if reason in {'max_tokens', 'model_context_window_exceeded', 'MAX_TOKENS', 'incomplete'}:
            normalized, status = 'length', 'incomplete'
        elif reason in {
            'refusal',
            'SAFETY',
            'RECITATION',
            'BLOCKLIST',
            'PROHIBITED_CONTENT',
            'SPII',
            'IMAGE_SAFETY',
            'IMAGE_PROHIBITED_CONTENT',
        }:
            normalized, status = 'content_filter', 'blocked'
        elif reason in {'pause_turn', 'CONTINUATION'}:
            normalized, status = 'pause', 'paused'
        elif reason in {'MALFORMED_FUNCTION_CALL', 'UNEXPECTED_TOOL_CALL', 'failed'}:
            normalized, status = 'error', 'failed'
        elif reason in {'end_turn', 'stop_sequence', 'tool_use', 'STOP', 'completed', 'requires_action'}:
            normalized, status = ('tool_calls' if tools else 'stop'), 'completed'
        else:
            normalized, status = 'unknown', 'incomplete'
        source = 'provider'
        if self.protocol == 'cpa-agy':
            source = (self.body.get('candidates') or [{}])[0].get('cpaFinishReasonSource', 'unspecified')
            if not isinstance(source, str):
                source = 'unspecified'
            if source.startswith('synthetic'):
                normalized, status = 'unknown', 'incomplete'
        return {
            'protocol': self.protocol,
            'reason': reason,
            'normalized': normalized,
            'status': status,
            **({'source': source} if self.protocol == 'cpa-agy' else {}),
        }

    def _tools(self):
        tools = []
        for block_index, block in enumerate(
            self.body.get('content', [])
            if self.protocol == 'anthropic'
            else (self.body.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
            if self.protocol in {'gemini', 'cpa-agy'}
            else self.body.get('steps', self.body.get('outputs', []))
        ):
            if block.get('type') == 'tool_use':
                tools.append(
                    {
                        'id': block['id'],
                        'type': 'function',
                        'function': {'name': block['name'], 'arguments': compact(block.get('input', {}))},
                    }
                )
            elif block.get('functionCall'):
                call = block['functionCall']
                tool_id = call.get('id') or self.generated_tool_ids.setdefault(block_index, 'call_' + str(uuid4()))
                tools.append(
                    {
                        'id': tool_id,
                        'type': 'function',
                        'function': {'name': call['name'], 'arguments': compact(call.get('args', {}))},
                    }
                )
            elif block.get('type') == 'function_call':
                tools.append(
                    {
                        'id': block['id'],
                        'type': 'function',
                        'function': {'name': block['name'], 'arguments': compact(block.get('arguments', {}))},
                    }
                )
        return [dict(t, index=i) for i, t in enumerate(tools)]

    def finish(self):
        self.finished = True
        tools = self._tools()
        ending = self.ending(tools)
        return chunk(
            {'tool_calls': tools} if tools and ending['normalized'] == 'tool_calls' else {},
            usage=self._usage(),
            finish=ending['normalized'],
            oi_finish=ending,
            oi_usage=self.metering(),
            oi_usage_protocol=self.protocol,
        )

    def feed(self, data):
        events = []
        if data.get('error') or data.get('type') == 'error':
            self.body['error'] = data.get('error', data)
            return [{'error': self.body['error']}]
        if self.protocol in {'openai-chat', 'openai-responses'}:
            if data.get('response'):
                self.body = merge(self.body, data['response'])
            if data.get('type') == 'response.output_item.done':
                self.blocks[data['output_index']] = data['item']
                self.body['output'] = [copy.deepcopy(self.blocks[i]) for i in sorted(self.blocks)]
            if data.get('choices'):
                delta = data['choices'][0].get('delta') or data['choices'][0].get('message') or {}
                msg = self.body.setdefault('choices', [{'message': {'role': 'assistant', 'content': ''}}])[0]['message']
                for k, v in delta.items():
                    if k == 'role':
                        msg[k] = v
                    elif isinstance(v, str):
                        msg[k] = (msg.get(k) or '') + v
                    elif k == 'reasoning_details':
                        from open_webui.utils.middleware import merge_streamed_reasoning_details

                        merge_streamed_reasoning_details(msg.setdefault(k, []), v)
                    elif k == 'provider_specific_fields' and isinstance(v, dict):
                        from open_webui.utils.middleware import merge_streamed_reasoning_details

                        fields = msg.setdefault(k, {})
                        for field, value in v.items():
                            if field == 'reasoning_details':
                                merge_streamed_reasoning_details(fields.setdefault(field, []), value)
                            else:
                                fields[field] = copy.deepcopy(value)
                    elif k == 'tool_calls':
                        for t in v or []:
                            old = self.blocks.setdefault(
                                t.get('index', 0),
                                {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}},
                            )
                            if t.get('id'):
                                old['id'] = t['id']
                            for key, value in t.get('function', {}).items():
                                old['function'][key] = (old['function'].get(key) or '') + (value or '')
                        msg['tool_calls'] = list(self.blocks.values())
                    else:
                        msg[k] = copy.deepcopy(v)
                if data['choices'][0].get('finish_reason'):
                    self.finished = True
            if data.get('type') in {'response.completed', 'response.failed', 'response.incomplete'}:
                self.finished = True
            self.usage = data.get('usage') or self.body.get('usage') or self.usage
            return [data]
        if self.protocol == 'anthropic':
            kind = data.get('type')
            if kind == 'message_start':
                self.body = copy.deepcopy(data['message'])
                self.usage = dict(self.body.get('usage') or {})
                if self.usage:
                    events.append(self.usage_event())
            elif kind == 'content_block_start':
                self.blocks[data['index']] = copy.deepcopy(data['content_block'])
                b = data['content_block']
                if b.get('text'):
                    events.append(chunk({'content': b['text']}))
                if b.get('type') == 'thinking' and b.get('thinking'):
                    events.append(chunk({'reasoning_content': b['thinking']}))
            elif kind == 'content_block_delta':
                b = self.blocks.setdefault(data['index'], {})
                d = data['delta']
                kind = d.get('type')
                if kind == 'input_json_delta':
                    b['_json'] = b.get('_json', '') + d.get('partial_json', '')
                elif kind == 'citations_delta':
                    b.setdefault('citations', []).append(copy.deepcopy(d['citation']))
                else:
                    for k, v in d.items():
                        if k == 'type':
                            continue
                        b[k] = b.get(k, '') + v if isinstance(v, str) else copy.deepcopy(v)
                    if kind == 'text_delta':
                        events.append(chunk({'content': d.get('text', '')}))
                    if kind == 'thinking_delta':
                        events.append(chunk({'reasoning_content': d.get('thinking', '')}))
            elif kind == 'content_block_stop':
                b = self.blocks[data['index']]
                if '_json' in b:
                    b['input'] = json.loads(b.pop('_json') or '{}')
            elif kind == 'message_delta':
                self.body.update(data.get('delta') or {})
                self.usage.update(data.get('usage') or {})
                self.body['usage'] = copy.deepcopy(self.usage)
                if self.usage:
                    events.append(self.usage_event())
            elif kind == 'message_stop':
                self.body['content'] = [self.blocks[i] for i in sorted(self.blocks)]
                self.body['usage'] = self.usage
                events.append(self.finish())
        elif self.protocol in {'gemini', 'cpa-agy'}:
            # CPA may wrap the native response, but native extensions remain intact inside it.
            data = data.get('response', data)
            self.usage = data.get('usageMetadata') or data.get('cpaUsageMetadata') or self.usage
            for key, value in data.items():
                if key != 'candidates':
                    self.body[key] = copy.deepcopy(value)
            candidates = data.get('candidates') or []
            if candidates:
                candidate = candidates[0]
                old = self.body.setdefault('candidates', [{'content': {'role': 'model', 'parts': []}}])[0]
                old.update({k: copy.deepcopy(v) for k, v in candidate.items() if k != 'content'})
                target = old['content']['parts']
                for part in candidate.get('content', {}).get('parts', []):
                    part = copy.deepcopy(part)
                    images = inline_images([part])
                    if images:
                        events.append(chunk({'images': images}))
                    if 'text' in part:
                        key = 'reasoning_content' if part.get('thought') else 'content'
                        events.append(chunk({key: part['text']}))
                        if (
                            target
                            and 'text' in target[-1]
                            and target[-1].get('thought', False) == part.get('thought', False)
                            and not target[-1].get('thoughtSignature')
                        ):
                            text = target[-1]['text'] + part['text']
                            target[-1].update(part)
                            target[-1]['text'] = text
                        else:
                            target.append(part)
                    else:
                        target.append(part)
                if candidate.get('finishReason'):
                    events.append(self.finish())
            elif data.get('promptFeedback', {}).get('blockReason'):
                events.append(self.finish())
            if self.usage and not any('usage' in event for event in events):
                events.append(self.usage_event())
        elif self.protocol == 'gemini-interactions':
            kind = data.get('event_type')
            if kind == 'interaction.created':
                self.body = copy.deepcopy(data['interaction'])
            elif kind == 'step.start':
                self.blocks[data['index']] = copy.deepcopy(data['step'])
                step = data['step']
                initial = content_text(step.get('content', [])) if step.get('type') == 'model_output' else ''
                if initial:
                    events.append(chunk({'content': initial}))
                if step.get('type') == 'thought' and step.get('summary'):
                    events.append(chunk({'reasoning_content': content_text(step['summary'])}))
            elif kind == 'step.delta':
                b = self.blocks.setdefault(data['index'], {})
                d = data['delta']
                t = d.get('type')
                if t == 'arguments_delta':
                    b['_json'] = b.get('_json', '') + d.get('arguments', '')
                elif t == 'thought_signature':
                    b['signature'] = b.get('signature', '') + d.get('signature', '')
                elif t == 'thought_summary':
                    b.setdefault('summary', []).append(copy.deepcopy(d.get('content')))
                    events.append(chunk({'reasoning_content': content_text([d.get('content') or {}])}))
                elif t == 'text':
                    content = b.setdefault('content', [])
                    if content and content[-1].get('type') == 'text':
                        content[-1]['text'] += d.get('text', '')
                    else:
                        content.append(copy.deepcopy(d))
                    events.append(chunk({'content': d.get('text', '')}))
                else:
                    b.setdefault('content', []).append(copy.deepcopy(d))
            elif kind == 'step.stop':
                b = self.blocks[data['index']]
                if '_json' in b:
                    b['arguments'] = json.loads(b.pop('_json') or '{}')
            elif kind in {'interaction.completed', 'interaction.failed'}:
                self.body.update(data.get('interaction') or {})
                self.body['steps'] = [self.blocks[i] for i in sorted(self.blocks)]
                self.usage = self.body.get('usage') or {}
                if kind.endswith('failed') and self.usage:
                    events.append(self.usage_event())
                events.append(
                    self.finish()
                    if kind.endswith('completed')
                    else {'error': self.body.get('error', 'Interaction failed')}
                )
        if self.protocol == 'anthropic' and self.blocks:
            self.body['content'] = [copy.deepcopy(self.blocks[i]) for i in sorted(self.blocks)]
        elif self.protocol == 'gemini-interactions' and self.blocks:
            self.body['steps'] = [copy.deepcopy(self.blocks[i]) for i in sorted(self.blocks)]
        return events

    def nonstream(self, body):
        self.body = copy.deepcopy(body.get('response', body))
        if self.protocol == 'openai-chat':
            return body
        if self.protocol == 'openai-responses':
            from open_webui.routers.openai import convert_responses_result

            return convert_responses_result(body)
        if self.protocol == 'ollama':
            from open_webui.utils.response import convert_response_ollama_to_openai

            return convert_response_ollama_to_openai(body)
        self.usage = self.body.get('usageMetadata') or self.body.get('usage') or {}
        blocks = (
            self.body.get('content', [])
            if self.protocol == 'anthropic'
            else (self.body.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
            if self.protocol in {'gemini', 'cpa-agy'}
            else [p for s in self.body.get('steps', self.body.get('outputs', [])) for p in s.get('content', [s])]
        )
        message = {
            'role': 'assistant',
            'content': ''.join(
                b.get('text', '') for b in blocks if not b.get('thought') and b.get('type') != 'thinking'
            ),
        }
        reasoning = ''.join(
            b.get('thinking', '')
            if b.get('type') == 'thinking'
            else b.get('text', '')
            if b.get('thought')
            else content_text(b.get('summary', []))
            if b.get('type') == 'thought'
            else ''
            for b in blocks
        )
        if reasoning:
            message['reasoning_content'] = reasoning
        images = inline_images(blocks)
        if images:
            message['images'] = images
        tools = self._tools()
        ending = self.ending(tools)
        if tools and ending['normalized'] == 'tool_calls':
            message['tool_calls'] = [{k: v for k, v in t.items() if k != 'index'} for t in tools]
        return {
            'id': self.body.get('id', str(uuid4())),
            'choices': [{'index': 0, 'message': message, 'finish_reason': ending['normalized']}],
            'oi_finish': ending,
            'usage': self._usage(),
            'oi_usage': self.metering(),
            'oi_usage_protocol': self.protocol,
        }


async def save_native(
    metadata, binding, call_id, body, replayable=True, *, caller_id=None, configuration_owner_id=None
):
    chat_id, message_id = metadata.get('chat_id'), metadata.get('message_id')
    if metadata.get('task') or not is_saved_chat_id(chat_id) or not message_id or not body:
        return
    previous = await Chats.get_message_by_id_and_message_id(chat_id, message_id) or {}
    calls = [item for item in previous.get('oi_native', []) if item.get('call_id') != call_id]
    calls.append(
        {
            'call_id': call_id,
            'binding_id': binding.id,
            'protocol': binding.protocol,
            'body': body,
            'replayable': replayable,
            'caller_id': caller_id,
            'configuration_owner_id': configuration_owner_id,
        }
    )
    await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, {'oi_native': calls}, touch=False)


async def generate(request, form, user, binding, connection, call_id, transport=None):
    from open_webui.utils.oi.replay import remember_native

    binding, connection = snapshot_target(binding, connection)
    url, payload = build_request(form, binding, connection)
    metadata = form.get('metadata') or getattr(request.state, 'metadata', {}) or {}
    rp_chat_id = (
        metadata.get('oi_temporary_id') if not is_saved_chat_id(metadata.get('chat_id')) else metadata.get('chat_id')
    )
    headers = catalog.request_headers(
        connection, user_id=user.id, chat_id=rp_chat_id or metadata.get('chat_id'), protocol=binding.protocol
    )
    if binding.protocol == 'anthropic':
        headers.setdefault('anthropic-version', '2023-06-01')
    headers['Content-Type'] = 'application/json'
    # Connect is bounded; long RP generations are not aborted by an arbitrary read timeout.
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=30, read=None, write=60, pool=30), trust_env=True, transport=transport
    )
    try:
        response = await client.send(
            client.build_request(
                'POST', url, json=payload, headers=headers, extensions={'oi_stream': bool(form.get('stream'))}
            ),
            stream=bool(form.get('stream')),
        )
    except BaseException:
        await client.aclose()
        raise
    if response.status_code >= 400:
        raw = await response.aread()
        await response.aclose()
        await client.aclose()
        try:
            error = json.loads(raw)
        except ValueError:
            error = {'error': {'message': f'Provider returned HTTP {response.status_code}'}}
        # No retry/fallback, especially CPA's account quarantine boundary at 429.
        return JSONResponse(
            error,
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() in {'retry-after', 'x-request-id'}},
        )
    state = NativeStream(binding.protocol)
    state.input_media = binding.protocol in {'gemini', 'cpa-agy'} and any(
        part.get('inlineData') or part.get('fileData')
        for content in payload.get('contents', [])
        for part in content.get('parts', [])
    )

    def remember():
        remember_native(
            request,
            metadata,
            {
                'call_id': call_id,
                'binding_id': binding.id,
                'protocol': binding.protocol,
                'body': state.body,
                'replayable': not form.get('stream') or (state.finished and not state.body.get('error')),
            },
        )

    if not form.get('stream'):
        try:
            body = response.json()
            result = state.nonstream(body)
            remember()
            await save_native(
                metadata,
                binding,
                call_id,
                state.body,
                caller_id=user.id,
                configuration_owner_id=getattr(connection, 'owner_id', user.id),
            )
            if not is_saved_chat_id(metadata.get('chat_id')):
                from open_webui.utils.oi.temporary_native import response_state

                result['oi_native_state'] = response_state(request, metadata, user, result['choices'][0]['message'])
            return result
        finally:
            await response.aclose()
            await client.aclose()

    async def stream():
        decoder = SSEDecoder()
        try:
            if binding.protocol == 'ollama':
                from open_webui.utils.response import convert_ollama_tool_call_to_openai, convert_ollama_usage_to_openai

                tool_count = 0
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    raw_message = data.get('message') or {}
                    saved = state.body.setdefault('message', {'role': 'assistant', 'content': ''})
                    for key, value in raw_message.items():
                        if key in {'content', 'thinking'}:
                            saved[key] = saved.get(key, '') + value
                        elif key == 'tool_calls':
                            saved.setdefault(key, []).extend(copy.deepcopy(value))
                        else:
                            saved[key] = copy.deepcopy(value)
                    state.body.update({key: copy.deepcopy(value) for key, value in data.items() if key != 'message'})
                    delta = copy.deepcopy(raw_message)
                    if 'thinking' in delta:
                        delta['reasoning_content'] = delta.pop('thinking')
                    if delta.get('tool_calls'):
                        calls = copy.deepcopy(delta['tool_calls'])
                        for index, call in enumerate(calls):
                            call.setdefault('index', call.get('function', {}).get('index', tool_count + index))
                        delta['tool_calls'] = convert_ollama_tool_call_to_openai(calls)
                        tool_count = max(tool_count, max(call['index'] for call in delta['tool_calls']) + 1)
                    finish = data.get('done_reason', 'stop') if data.get('done') else None
                    if finish == 'stop' and tool_count:
                        finish = 'tool_calls'
                    yield (
                        'data: '
                        + compact(
                            chunk(
                                delta,
                                usage=convert_ollama_usage_to_openai(data) if data.get('done') else None,
                                finish=finish,
                                **(
                                    {'oi_usage': accounting.ollama_metering(data), 'oi_usage_protocol': 'ollama'}
                                    if data.get('done')
                                    else {}
                                ),
                            )
                        )
                        + '\n\n'
                    )
                    if data.get('done'):
                        state.finished = True
            else:
                async for raw in response.aiter_bytes():
                    for data in decoder.feed(raw):
                        for event in state.feed(data):
                            yield 'data: ' + compact(event) + '\n\n'
                for data in decoder.feed(b'', final=True):
                    for event in state.feed(data):
                        yield 'data: ' + compact(event) + '\n\n'
            if not state.finished:
                yield (
                    'data: '
                    + compact(
                        {
                            'error': {
                                'message': 'Provider stream ended without a completion marker',
                                'code': 'incomplete_stream',
                            }
                        }
                    )
                    + '\n\n'
                )
            remember()
            if not is_saved_chat_id(metadata.get('chat_id')):
                from open_webui.utils.oi.temporary_native import response_state

                projection = NativeStream(binding.protocol).nonstream(copy.deepcopy(state.body))['choices'][0][
                    'message'
                ]
                yield (
                    'data: '
                    + compact({'oi_native_state': response_state(request, metadata, user, projection)})
                    + '\n\n'
                )
            yield 'data: [DONE]\n\n'
        finally:
            remember()
            await response.aclose()
            await client.aclose()
            await save_native(
                metadata,
                binding,
                call_id,
                state.body,
                state.finished and not state.body.get('error'),
                caller_id=user.id,
                configuration_owner_id=getattr(connection, 'owner_id', user.id),
            )

    return StreamingResponse(stream(), media_type='text/event-stream')
