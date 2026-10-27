"""Local context observations. This code never changes a request or calls a model."""

import json
import math
import re

DATA_URL = re.compile(r'data:[^;,\s]+;base64,[A-Za-z0-9+/=]+')


def measure(value):
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    # Deliberately named estimate: provider tokenization and media costs are not inferred.
    return math.ceil(len(DATA_URL.sub('', text)) / 4)


def observe_context(form, capabilities=None):
    capabilities = capabilities or {}
    groups = {'system': 0, 'messages': 0, 'tool_results': 0, 'tool_definitions': 0}
    media_unknown = False
    for message in form.get('messages', []):
        content = message.get('content', '')
        if isinstance(content, list):
            media_unknown |= any(
                p.get('type') not in {'text', 'input_text', 'output_text'} for p in content if isinstance(p, dict)
            )
        role = message.get('role')
        category = 'system' if role in {'system', 'developer'} else 'tool_results' if role == 'tool' else 'messages'
        groups[category] += measure(
            {k: v for k, v in message.items() if k in {'role', 'content', 'tool_calls', 'tool_call_id', 'name'}}
        )
    tools = []
    for tool in form.get('tools', []):
        size = measure(tool)
        groups['tool_definitions'] += size
        tools.append(
            {
                'name': tool.get('function', {}).get('name') or tool.get('name') or tool.get('type'),
                'estimated_tokens': size,
            }
        )
    metadata = form.get('metadata') or {}
    return {
        'purpose': metadata.get('task')
        or ('tool_followup' if (form.get('messages') or [{}])[-1].get('role') == 'tool' else 'assistant'),
        'source': 'estimated',
        'method': 'unicode_characters_divided_by_4',
        'categories': groups,
        'tools': tools,
        'estimated_tokens': sum(groups.values()),
        'media_tokens': None if media_unknown else 0,
        'input_limit': capabilities.get('input_limit'),
        'context_window': capabilities.get('context_window'),
        'output_limit': capabilities.get('output_limit'),
        'reserved_output': form.get(
            'max_completion_tokens',
            form.get(
                'max_tokens',
                form.get(
                    'max_output_tokens',
                    (form.get('generationConfig') or {}).get(
                        'maxOutputTokens', (form.get('generation_config') or {}).get('max_output_tokens')
                    ),
                ),
            ),
        ),
        'skills': {'count': len(metadata.get('skill_ids') or []), 'tokens': None},
        'subagents': {'tokens': None},
        'mcp': {'tokens': None},
        'compaction': {'automatic': bool(metadata.get('oi_auto_compaction', False)), 'summary_tokens': None},
    }
