"""Wire protocols, independent of templates and execution transport."""

from copy import deepcopy

PROTOCOL_REGISTRY = {
    'openai-chat': {'label': 'OpenAI · Chat Completions', 'prefix': '/v1', 'generate': '/chat/completions'},
    'openai-responses': {'label': 'OpenAI · Responses', 'prefix': '/v1', 'generate': '/responses'},
    'anthropic': {'label': 'Anthropic · Messages', 'prefix': '/v1', 'generate': '/messages'},
    'gemini': {'label': 'Gemini · GenerateContent', 'prefix': '/v1beta', 'generate': '/models/{model}:generateContent'},
    'gemini-interactions': {
        'label': 'Gemini · Interactions',
        'prefix': '/v1beta',
        'generate': '/interactions',
        'extension': True,
    },
    'ollama': {'label': 'Ollama · Native', 'prefix': '', 'generate': '/api/chat', 'extension': True},
}
PROTOCOL_ALIASES = {'cpa-agy': 'gemini'}
PROTOCOLS = set(PROTOCOL_REGISTRY) | set(PROTOCOL_ALIASES)
COMMON_PROTOCOLS = [key for key, value in PROTOCOL_REGISTRY.items() if not value.get('extension')]


def canonical_protocol(value):
    return PROTOCOL_ALIASES.get(value, value)


def protocol_directory():
    return [
        {
            'id': key,
            **deepcopy(value),
            'browser_supported': key in COMMON_PROTOCOLS,
            'discovery_path': '/api/tags' if key == 'ollama' else '/models',
        }
        for key, value in PROTOCOL_REGISTRY.items()
    ]
