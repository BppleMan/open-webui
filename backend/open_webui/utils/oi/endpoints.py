"""One resolver for discovery, calls, browser RPC and configuration previews.

Routing v1 is persisted. Its legacy conversion is also used by OI7 migration;
subsequent routing versions must retain this conversion contract.
"""

from copy import deepcopy
from urllib.parse import quote, urlsplit, urlunsplit
from open_webui.utils.oi.protocol_registry import PROTOCOL_REGISTRY, canonical_protocol


def profile_for_template(template):
    return (
        template if template in {'cpa-agy', 'azure-openai', 'openrouter', 'gemini', 'zai', 'deepseek'} else 'standard'
    )


def compatibility_profile(connection):
    routing = (connection.settings or {}).get('routing')
    return routing.get('profile', 'standard') if routing else profile_for_template(connection.template)


def normalize_service_address(base, settings, protocol, template='openai-compatible', *, legacy=False):
    settings = deepcopy(settings or {})
    old_routing = settings.get('routing')
    if old_routing is not None and not isinstance(old_routing, dict):
        raise ValueError('Routing settings must be an object')
    routing = deepcopy(
        old_routing if old_routing is not None else {'version': 1, 'profile': profile_for_template(template)}
    )
    if routing.get('version', 1) != 1:
        raise ValueError('Unsupported routing settings version')
    if routing.get('profile', 'standard') not in {
        'standard',
        'cpa-agy',
        'azure-openai',
        'openrouter',
        'gemini',
        'zai',
        'deepseek',
    }:
        raise ValueError('Unknown compatibility profile')
    custom_prefixes = routing.get('prefixes', {})
    if not isinstance(custom_prefixes, dict) or any(
        key not in PROTOCOL_REGISTRY
        or not isinstance(value, str)
        or (value and (not value.startswith('/') or value.startswith('//')))
        or any(part in value for part in ('..', '?', '#', '\\'))
        for key, value in custom_prefixes.items()
    ):
        raise ValueError('Protocol route prefixes must be relative paths')
    routing.setdefault('version', 1)
    profile = routing.get('profile', 'standard')
    url = urlsplit(base.rstrip('/'))
    path = url.path.rstrip('/')
    mode = canonical_protocol(protocol)
    expected = PROTOCOL_REGISTRY[mode]['prefix']
    prefixes = routing.setdefault('prefixes', {})
    if legacy and profile == 'azure-openai':
        # The old Azure adapter treated the entire address as its deployment mount.
        pass
    elif path.endswith('/v1beta/openai'):
        path = path[: -len('/v1beta/openai')]
        prefixes.update({'openai-chat': '/v1beta/openai', 'openai-responses': '/v1beta/openai'})
    elif path.endswith('/api/paas/v4'):
        path = path[: -len('/api/paas/v4')]
        prefixes.update({'openai-chat': '/api/paas/v4', 'openai-responses': '/api/paas/v4'})
    elif path.endswith(('/v1', '/v1beta')):
        suffix = '/v1beta' if path.endswith('/v1beta') else '/v1'
        path = path[: -len(suffix)]
        if legacy and suffix != expected and protocol != 'cpa-agy':
            prefixes[mode] = suffix
    elif legacy and not old_routing and mode != 'ollama' and profile not in {'azure-openai', 'cpa-agy'}:
        routing['legacy_prefix'] = True
        routing['needs_review'] = True
    if profile == 'openrouter' and url.hostname == 'openrouter.ai' and not path:
        path = '/api'
    if profile == 'gemini':
        prefixes.setdefault('openai-chat', '/v1beta/openai')
        prefixes.setdefault('openai-responses', '/v1beta/openai')
    if profile == 'zai':
        prefixes.setdefault('openai-chat', '/api/paas/v4')
        prefixes.setdefault('openai-responses', '/api/paas/v4')
    settings['routing'] = routing
    if profile == 'openrouter':
        settings.setdefault('cost_currency', 'USD')
    return urlunsplit((url.scheme, url.netloc, path, url.query, url.fragment)), settings


def api_base(connection, protocol=None):
    protocol = protocol or connection.protocol
    base = connection.base_url.rstrip('/')
    routing = (connection.settings or {}).get('routing')
    if not routing:
        # Read compatibility for pre-OI7 archives and synthetic legacy fixtures.
        if protocol != 'cpa-agy':
            return base
        url = urlsplit(base)
        path = url.path.rstrip('/')
        if path.endswith('/v1'):
            path = path[:-3]
        if not path.endswith('/v1beta'):
            path += '/v1beta'
        return urlunsplit((url.scheme, url.netloc, path, url.query, url.fragment))
    mode = canonical_protocol(protocol)
    if routing.get('legacy_prefix'):
        return base
    prefix = (routing.get('prefixes') or {}).get(mode, PROTOCOL_REGISTRY[mode]['prefix'])
    return base + prefix


def resolve_endpoint(connection, protocol, operation='generate', *, model=None, stream=False, path=None):
    mode = canonical_protocol(protocol)
    spec = PROTOCOL_REGISTRY[mode]
    if mode == 'gemini' and model:
        model = model.removeprefix('models/')
    if operation == 'models':
        return api_base(connection, protocol) + ('/api/tags' if mode == 'ollama' else '/models')
    if operation == 'generate':
        path = spec['generate']
        if mode == 'gemini' and stream:
            path = '/models/{model}:streamGenerateContent?alt=sse'
        path = path.replace('{model}', quote(model, safe='') if model else '{model}')
    if not path or not path.startswith('/') or path.startswith('//') or '..' in path:
        raise ValueError('Invalid protocol operation path')
    if compatibility_profile(connection) == 'azure-openai' and mode == 'openai-chat':
        deployment = connection.settings.get('deployment') or model
        version = connection.settings.get('api_version')
        if not version:
            raise ValueError('Azure requires an explicit API version')
        deployment = quote(deployment, safe='') if deployment else '{deployment}'
        return f'{connection.base_url.rstrip("/")}/openai/deployments/{deployment}{path}?api-version={quote(version, safe="")}'
    return api_base(connection, protocol) + path
