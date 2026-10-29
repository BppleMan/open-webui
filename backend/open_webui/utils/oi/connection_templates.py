"""Versioned creation defaults. Saved connections never inherit later template changes."""

from copy import deepcopy

from open_webui.utils.oi.protocol_registry import PROTOCOLS, PROTOCOL_REGISTRY, canonical_protocol
from open_webui.utils.oi.endpoints import normalize_service_address

AUTH_TYPES = {'bearer', 'x-api-key', 'x-goog-api-key', 'api-key', 'none'}
TEMPLATE_VERSION = 2


def template(id, name, protocol, base_url, auth='bearer', **options):
    return {
        'id': id,
        'name': name,
        'version': TEMPLATE_VERSION,
        'protocol': protocol,
        'protocols': options.pop('protocols', [protocol]),
        'base_url': base_url,
        'auth': auth,
        'settings': {'auth': auth},
        'fields': [],
        'discovery': 'automatic',
        **options,
    }


OPENAI_PROTOCOLS = ['openai-chat', 'openai-responses']
TEMPLATES = [
    template(
        'openai',
        'OpenAI',
        'openai-responses',
        'https://api.openai.com/v1',
        protocols=['openai-responses', 'openai-chat'],
        description='Responses / Chat Completions',
    ),
    template(
        'anthropic',
        'Anthropic Claude',
        'anthropic',
        'https://api.anthropic.com/v1',
        'x-api-key',
        description='Messages 原生接口',
    ),
    template(
        'gemini',
        'Google Gemini',
        'gemini',
        'https://generativelanguage.googleapis.com/v1beta',
        'x-goog-api-key',
        protocols=['gemini', 'gemini-interactions'],
        description='GenerateContent / Interactions 原生接口',
    ),
    template('cpa-agy', 'CPA / AGY', 'cpa-agy', '', description='Gemini 原生 · CPA 管理账号与调度'),
    template(
        'new-api',
        'New API',
        'openai-chat',
        '',
        protocols=OPENAI_PROTOCOLS + ['anthropic', 'gemini'],
        description='自托管模型网关',
    ),
    template(
        'openrouter',
        'OpenRouter',
        'openai-chat',
        'https://openrouter.ai/api/v1',
        protocols=OPENAI_PROTOCOLS,
        description='多供应商模型网关',
    ),
    template('ollama', 'Ollama', 'ollama', 'http://localhost:11434', 'none', description='本地模型服务'),
    template(
        'lm-studio',
        'LM Studio',
        'openai-chat',
        'http://localhost:1234/v1',
        'none',
        protocols=OPENAI_PROTOCOLS,
        description='本地 OpenAI 兼容服务',
    ),
    template(
        'azure-openai',
        'Azure OpenAI',
        'openai-chat',
        '',
        'api-key',
        discovery='manual',
        description='按部署名称添加模型',
        fields=[
            {'key': 'api_version', 'label': 'API 版本', 'required': True, 'placeholder': '填写资源所用的 API 版本'}
        ],
    ),
    template('deepseek', 'DeepSeek', 'openai-chat', 'https://api.deepseek.com', description='OpenAI 兼容接口'),
    template(
        'groq',
        'Groq',
        'openai-chat',
        'https://api.groq.com/openai/v1',
        protocols=OPENAI_PROTOCOLS,
        description='OpenAI 兼容接口',
    ),
    template('mistral', 'Mistral', 'openai-chat', 'https://api.mistral.ai/v1', description='OpenAI 兼容接口'),
    template(
        'moonshot',
        'Moonshot / Kimi',
        'openai-chat',
        'https://api.moonshot.cn/v1',
        protocols=OPENAI_PROTOCOLS,
        description='OpenAI 兼容接口',
    ),
    template(
        'xai', 'xAI', 'openai-chat', 'https://api.x.ai/v1', protocols=OPENAI_PROTOCOLS, description='OpenAI 兼容接口'
    ),
    template('zai', 'Z.ai', 'openai-chat', 'https://api.z.ai/api/paas/v4', description='按量计费 API'),
    template(
        'xiaomi',
        'Xiaomi MiMo',
        'openai-chat',
        'https://api.xiaomimimo.com/v1',
        protocols=OPENAI_PROTOCOLS,
        description='按量计费 API',
    ),
    template(
        'openai-compatible',
        '通用 OpenAI 兼容',
        'openai-chat',
        '',
        protocols=OPENAI_PROTOCOLS,
        description='自定义地址与凭据',
    ),
]


# Templates seed independent persisted routing; their old protocol lists are only recommendations.
for _item in TEMPLATES:
    _item['recommended_protocols'] = list(dict.fromkeys(canonical_protocol(p) for p in _item['protocols']))
    _item['protocols'] = list(PROTOCOL_REGISTRY)
    _item['protocol'] = canonical_protocol(_item['protocol'])
    _item['base_url'], _item['settings'] = normalize_service_address(
        _item['base_url'], _item['settings'], _item['protocol'], _item['id']
    )
    for _field in _item['fields']:
        if _item['id'] == 'azure-openai':
            _field['protocols'] = ['openai-chat']


def get_template(template_id):
    value = next((item for item in TEMPLATES if item['id'] == template_id), None)
    if value is None:
        raise ValueError('Unknown connection template')
    return deepcopy(value)


def connection_defaults(form, existing=None):
    """Apply defaults once; updates preserve saved overrides and the template version."""
    selected = get_template(form.template)
    if existing is not None and existing.template != form.template:
        raise ValueError('Create a new connection to use a different template')
    protocol = canonical_protocol(
        form.protocol or (existing.protocol if existing is not None else selected['protocol'])
    )
    if protocol not in PROTOCOLS:
        raise ValueError('Unsupported protocol')
    base_url = (
        form.base_url
        if form.base_url is not None
        else (existing.base_url if existing is not None else selected['base_url'])
    )
    settings = deepcopy(existing.settings if existing is not None else selected['settings'])
    if form.settings is not None:
        settings.update(deepcopy(form.settings))
    if existing is None:
        settings['template_version'] = selected['version']
    elif 'template_version' in existing.settings:
        settings['template_version'] = existing.settings['template_version']
    if existing is not None and base_url.rstrip('/') != existing.base_url.rstrip('/'):
        routing = settings.get('routing') or {}
        routing.pop('legacy_prefix', None)
        routing.pop('needs_review', None)
    base_url, settings = normalize_service_address(base_url, settings, protocol, form.template)
    if settings.get('auth', selected['auth']) not in AUTH_TYPES:
        raise ValueError('Unsupported authentication method')
    for field in selected['fields']:
        if (
            protocol in field.get('protocols', [protocol])
            and field.get('required')
            and not str(settings.get(field['key']) or '').strip()
        ):
            raise ValueError(f'{field["label"]} is required')
    return {'protocol': protocol, 'base_url': base_url, 'settings': settings}
