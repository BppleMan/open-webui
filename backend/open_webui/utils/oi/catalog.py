"""Connection-scoped model identities and credentials. Display names never route requests."""

import base64
import hashlib
import json
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from open_webui.env import WEBUI_SECRET_KEY
from open_webui.models.customization import Connection, ModelBinding
from open_webui.utils.oi.connection_templates import (
    PROTOCOLS as PROTOCOLS,
)
from open_webui.utils.oi.connection_templates import (
    TEMPLATES as TEMPLATES,
)
from open_webui.utils.oi.connection_templates import (
    connection_defaults as connection_defaults,
)
from open_webui.utils.oi.connection_templates import (
    get_template as get_template,
)
from open_webui.utils.oi.storage import identifier, now
from open_webui.utils.oi.endpoints import resolve_endpoint, compatibility_profile
from open_webui.utils.oi.selection import effective_protocol, snapshot_target
from open_webui.utils.oi.protocol_registry import canonical_protocol
from sqlalchemy import select


def _cipher():
    if not WEBUI_SECRET_KEY:
        raise ValueError('A persistent WEBUI_SECRET_KEY is required for connection credentials')
    digest = hashlib.sha256(('oi.connection.v1:' + WEBUI_SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_credentials(value):
    return _cipher().encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()


def credentials(connection):
    try:
        return json.loads(_cipher().decrypt(connection.credentials.encode())) if connection.credentials else {}
    except (InvalidToken, ValueError):
        raise ValueError('Connection credentials cannot be decrypted with the current server key') from None


def identity_visible(connection, user):
    """Historical identity remains relevant after soft removal; never resolve its alias to a sibling."""
    if getattr(connection, 'transport', None) == 'browser':
        return bool(user and connection.owner_id == user.id)
    return bool(connection.shared or (user and (connection.owner_id == user.id or user.role == 'admin')))


def visible(connection, user):
    if (connection.settings or {}).get('deleted'):
        return False
    return identity_visible(connection, user)


def editable(connection, user):
    if getattr(connection, 'transport', None) == 'browser':
        return bool(user and connection.owner_id == user.id)
    return bool(user and (connection.owner_id == user.id or user.role == 'admin'))


def connection_json(connection, user):
    result = {
        key: getattr(connection, key)
        for key in (
            'id',
            'name',
            'template',
            'protocol',
            'transport',
            'shared',
            'enabled',
            'revision',
            'created_at',
            'updated_at',
        )
    }
    if editable(connection, user):
        result.update(
            base_url=connection.base_url,
            settings=connection.settings,
            has_credentials=bool(connection.credentials),
            owner_id=connection.owner_id,
            deleted=bool((connection.settings or {}).get('deleted')),
        )
    return result


def capabilities(binding):
    config = binding.capabilities or {}
    if 'discovered' in config or 'overrides' in config:
        return {**config.get('discovered', {}), **config.get('overrides', {})}
    return config


def binding_json(binding, connection):
    return {
        key: getattr(binding, key)
        for key in (
            'id',
            'connection_id',
            'upstream_id',
            'protocol',
            'name',
            'enabled',
            'available',
            'pricing',
            'params',
            'aliases',
        )
    } | {
        'protocol': effective_protocol(binding, connection),
        'effective_protocol': effective_protocol(binding, connection),
        'protocol_override': getattr(binding, 'protocol_override', None)
        if (connection.settings or {}).get('routing')
        else (
            canonical_protocol(binding.protocol)
            if canonical_protocol(binding.protocol) != canonical_protocol(connection.protocol)
            else None
        ),
        'protocol_source': 'model'
        if (
            getattr(binding, 'protocol_override', None)
            if (connection.settings or {}).get('routing')
            else canonical_protocol(binding.protocol) != canonical_protocol(connection.protocol)
        )
        else 'connection',
        'connection_name': connection.name,
        'transport': getattr(connection, 'transport', None) or 'server',
        'capabilities': capabilities(binding),
        'capability_overrides': (binding.capabilities or {}).get('overrides', {}),
    }


async def binding_readable(db, binding, connection, user, *, management=False):
    if not visible(connection, user):
        return False
    if management and editable(connection, user):
        return True
    if not (connection.enabled and binding.enabled and binding.available):
        return False
    from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
    from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL
    from open_webui.utils.models import check_model_access

    if BYPASS_MODEL_ACCESS_CONTROL or (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL):
        return True
    try:
        await check_model_access(user, {'id': binding.id, 'oi_binding': True}, db=db)
        return True
    except Exception:
        return False


async def resolve(db, reference, user, *, require_enabled=True):
    pairs = (
        await db.execute(select(ModelBinding, Connection).join(Connection, ModelBinding.connection_id == Connection.id))
    ).all()
    matches = [(b, c) for b, c in pairs if visible(c, user) and (b.id == reference or reference in (b.aliases or []))]
    if not matches:
        raise LookupError('Unknown model binding')
    if len(matches) != 1:
        raise ValueError('Ambiguous legacy model ID; choose a connection-specific model')
    binding, connection = matches[0]
    if require_enabled and not (binding.enabled and binding.available and connection.enabled):
        raise ValueError('The selected connection or model is unavailable')
    return snapshot_target(binding, connection)


def reasoning_controls(binding):
    reasoning = capabilities(binding).get('reasoning') or {}
    levels = reasoning.get('levels')
    parameter = reasoning.get('parameter')
    mode = canonical_protocol(getattr(binding, 'protocol', ''))
    family = (
        'gemini'
        if str(parameter).startswith('generationConfig.')
        else 'anthropic'
        if str(parameter).startswith(('thinking.', 'output_config.'))
        else 'openai'
        if str(parameter).startswith(('reasoning.', 'reasoning_effort'))
        else None
    )
    current_family = 'openai' if mode in {'openai-chat', 'openai-responses'} else mode
    if family and mode and family != current_family:
        return {}  # Do not send the previous protocol's reasoning controls to the new endpoint.
    # A declared parameter is required: never guess a provider's wire semantics from its model name.
    if not parameter:
        return {}
    if levels:

        def parameters(value):
            result, target = {}, None
            target = result
            parts = parameter.split('.')
            if any(not part or part in {'__proto__', 'prototype', 'constructor'} for part in parts):
                raise ValueError('Invalid reasoning parameter path')
            for part in parts[:-1]:
                target[part] = {}
                target = target[part]
            target[parts[-1]] = value
            return result

        default_index = levels.index(reasoning['default']) if reasoning.get('default') in levels else None
        return {
            'oi_reasoning': {
                'label': 'Reasoning',
                'display': 'slider' if len(levels) > 1 else 'menu',
                'default': f'level_{default_index}' if default_index is not None else None,
                'options': {
                    f'level_{i}': {'label': str(level), 'params': parameters(level)} for i, level in enumerate(levels)
                },
            }
        }
    budget = reasoning.get('budget')
    if budget:
        from open_webui.models.models import ModelControl

        control = ModelControl(
            label='Reasoning budget',
            display='budget',
            budget={**budget, 'parameter': parameter},
        )
        return {'oi_reasoning': control.model_dump()}
    return {}


def thought_controls(binding):
    """Summary visibility is independent of the provider's reasoning budget/level."""
    if canonical_protocol(getattr(binding, 'protocol', '')) != 'gemini':
        return {}
    return {
        'oi_thoughts': {
            'label': 'Thought summaries',
            'description': 'Request summaries by default. Model settings may override this; reasoning effort is unchanged.',
            'default': None,
            'display': 'menu',
            'options': {
                key: {
                    'label': label,
                    'params': {
                        'provider_options': {'generationConfig': {'thinkingConfig': {'includeThoughts': enabled}}}
                    },
                }
                for key, label, enabled in [('on', 'Request summaries', True), ('off', 'Do not request', False)]
            },
        }
    }


async def model_catalog(db, user, *, internal=False):
    pairs = (
        await db.execute(select(ModelBinding, Connection).join(Connection, ModelBinding.connection_id == Connection.id))
    ).all()
    direct_enabled = True
    if not internal and any(connection.transport == 'browser' for _, connection in pairs):
        from open_webui.models.config import Config

        direct_enabled = bool(await Config.get('direct.enable'))
    # Include disabled/removed bindings in ambiguity counts. A disappearing row must
    # never turn an ambiguous historic reference into an automatic reroute. Internal
    # catalogs conservatively count all owners; public catalogs use the caller scope.
    from collections import Counter

    alias_counts = Counter(
        alias
        for binding, connection in pairs
        if internal or identity_visible(connection, user)
        for alias in set(binding.aliases or [])
    )
    result = []
    for binding, connection in pairs:
        if connection.transport == 'browser' and not direct_enabled:
            continue
        if not (connection.enabled and binding.enabled and binding.available):
            continue
        if not internal and not await binding_readable(db, binding, connection, user):
            continue
        binding, connection = snapshot_target(binding, connection)
        caps = capabilities(binding)
        controls = {
            **(binding.params or {}).get('model_controls', {}),
            **reasoning_controls(binding),
            **thought_controls(binding),
        }
        result.append(
            {
                'id': binding.id,
                'name': binding.name,
                'object': 'model',
                'created': 0,
                'owned_by': 'openai',
                'connection_type': connection.settings.get(
                    'connection_type', 'local' if binding.protocol == 'ollama' else 'external'
                ),
                'connection': {
                    'id': connection.id,
                    'name': connection.name,
                    'protocol': canonical_protocol(binding.protocol),
                    'transport': getattr(connection, 'transport', None) or 'server',
                },
                'direct': getattr(connection, 'transport', None) == 'browser',
                'oi_binding': True,
                'aliases': [alias for alias in binding.aliases or [] if alias_counts[alias] == 1],
                'upstream_id': binding.upstream_id,
                'capabilities': caps,
                'info': {
                    'id': binding.id,
                    'name': binding.name,
                    'params': {**(binding.params or {}), 'model_controls': controls},
                    'meta': {'capabilities': caps, 'oi_connection_id': connection.id},
                },
            }
        )
    return result


def validate_url(value):
    parsed = urlparse(value)
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError('Use an HTTP(S) base URL without embedded credentials, query or fragment')
    return value.rstrip('/')


def request_headers(connection, *, user_id=None, chat_id=None, protocol=None):
    secrets = credentials(connection)
    headers = {**(secrets.get('headers') or {})}
    key = secrets.get('api_key', '')
    template = next((t for t in TEMPLATES if t['id'] == connection.template), {})
    auth = connection.settings.get('auth', template.get('auth', 'bearer'))
    if key:
        if auth == 'bearer':
            headers['Authorization'] = f'Bearer {key}'
        elif auth != 'none':
            headers[auth] = key
    if (protocol or connection.protocol) == 'anthropic':
        headers.setdefault('anthropic-version', '2023-06-01')
    if compatibility_profile(connection) == 'cpa-agy' or protocol == 'cpa-agy':
        for key in list(headers):
            if key.lower().startswith('x-rp-'):
                del headers[key]
        if user_id is not None:
            if not chat_id:
                raise ValueError('CPA requires a stable conversation ID before the first request')
            headers.update({'X-RP-Client': 'openwebui', 'X-RP-User-ID': user_id, 'X-RP-Chat-ID': chat_id})
    return headers


def _model_page(body, protocol):
    if not isinstance(body, dict):
        raise ValueError('The provider did not return a model list')
    field = 'models' if protocol in {'ollama', 'gemini', 'gemini-interactions', 'cpa-agy'} else 'data'
    if protocol == 'cpa-agy' and field not in body:
        field = 'data'
    models = body.get(field)
    if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
        raise ValueError('The provider did not return a model list')
    cursor = None
    if protocol in {'gemini', 'gemini-interactions', 'cpa-agy'} and body.get('nextPageToken'):
        cursor = ('pageToken', body['nextPageToken'])
    elif protocol == 'anthropic' and body.get('has_more'):
        cursor = ('after_id', body.get('last_id'))
    if cursor and (not isinstance(cursor[1], str) or not cursor[1]):
        raise ValueError('The provider returned an invalid pagination cursor')
    return models, cursor


async def fetch_models(connection, transport=None):
    protocol = connection.protocol
    url = resolve_endpoint(connection, protocol, 'models')
    params, cursors, models = {}, set(), []
    async with httpx.AsyncClient(trust_env=True, timeout=30, transport=transport) as client:
        for _ in range(100):
            response = await client.get(url, params=params, headers=request_headers(connection))
            response.raise_for_status()
            page, cursor = _model_page(response.json(), protocol)
            models.extend(page)
            if cursor is None:
                return models
            if cursor in cursors:
                raise ValueError('Model discovery repeated a page')
            cursors.add(cursor)
            params = dict([cursor])
    raise ValueError('Model discovery exceeded the page limit; previous configuration was preserved')


def discovered_capabilities(model):
    result = dict(model.get('capabilities') or {})
    # OpenRouter advertises a shared input/output window. Do not turn it into an
    # independent input limit, or invent reasoning levels from a parameter name.
    provider = model.get('top_provider')
    provider = provider if isinstance(provider, dict) else {}

    def positive_integer(value):
        return type(value) is int and value > 0

    windows = [
        value for value in (model.get('context_length'), provider.get('context_length')) if positive_integer(value)
    ]
    if windows:
        result.setdefault('context_window', min(windows))
    if positive_integer(provider.get('max_completion_tokens')):
        result.setdefault('output_limit', provider['max_completion_tokens'])
    for source, target in (
        ('inputTokenLimit', 'input_limit'),
        ('outputTokenLimit', 'output_limit'),
        ('supportedGenerationMethods', 'methods'),
    ):
        if model.get(source):
            result[target] = model[source]
    thinking = model.get('thinkingSupport')
    if isinstance(thinking, dict):
        result['thinking_support'] = thinking
        if thinking.get('levels'):
            result['reasoning'] = {
                'parameter': 'generationConfig.thinkingConfig.thinkingLevel',
                'levels': thinking['levels'],
            }
        elif (
            isinstance(thinking.get('min'), int)
            and isinstance(thinking.get('max'), int)
            and thinking['max'] > thinking['min']
        ):
            result['reasoning'] = {
                'parameter': 'generationConfig.thinkingConfig.thinkingBudget',
                'budget': {'min': thinking['min'], 'max': thinking['max'], 'step': 1},
            }
    return result


async def discover(db, connection, transport=None):
    if getattr(connection, 'transport', None) == 'browser':
        raise ValueError('Browser connections must discover models from the browser')
    # Fetch all pages before changing availability; a partial failure must preserve the catalog.
    models = await fetch_models(connection, transport)
    return await apply_discovered_models(db, connection, models)


async def apply_discovered_models(db, connection, models):
    from sqlalchemy import update

    # A write lock also serializes SQLite; PostgreSQL holds it until commit.
    await db.execute(update(Connection).where(Connection.id == connection.id).values(updated_at=Connection.updated_at))
    protocol = canonical_protocol(connection.protocol)
    seen, result = set(), []
    for model in models:
        upstream = model.get('id') or model.get('name') or model.get('model')
        if not isinstance(upstream, str) or not upstream:
            raise ValueError('The provider returned a model without an ID')
        if protocol.startswith('gemini'):
            upstream = upstream.removeprefix('models/')
        if upstream in seen:
            continue
        seen.add(upstream)
        existing = (
            (
                await db.execute(
                    select(ModelBinding).where(
                        ModelBinding.connection_id == connection.id, ModelBinding.upstream_id == upstream
                    )
                )
            )
            .scalars()
            .all()
        )
        discovered = discovered_capabilities(model)
        if not existing:
            row = ModelBinding(
                id='oi:' + identifier(),
                connection_id=connection.id,
                upstream_id=upstream,
                protocol=protocol,
                protocol_override=None,
                name=model.get('displayName') or model.get('display_name') or model.get('name') or upstream,
                enabled=False,
                available=True,
                capabilities={'discovered': discovered, 'overrides': {}, 'manual': False},
                pricing={},
                params={},
                aliases=[],
                updated_at=now(),
            )
            db.add(row)
            existing = [row]
        for row in existing:
            old = row.capabilities or {}
            row.capabilities = {**old, 'discovered': discovered, 'overrides': old.get('overrides', {})}
            row.available = True
            row.updated_at = now()
            result.append(row)
    # Only a successful discovery can mark an absent model unavailable; never delete user overrides.
    for row in (await db.execute(select(ModelBinding).where(ModelBinding.connection_id == connection.id))).scalars():
        if row.upstream_id not in seen and not (row.capabilities or {}).get('manual'):
            row.available = False
    await db.flush()
    return result
