"""Explicit versioned configuration migration. Preview never returns credentials."""

from open_webui.utils.oi.endpoints import normalize_service_address
from open_webui.utils.oi.protocol_registry import canonical_protocol

import copy
import hashlib
import hmac
import json
from collections import Counter
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select, update
from open_webui.env import WEBUI_SECRET_KEY
from open_webui.models.config import Config
from open_webui.models.users import User
from open_webui.models.models import Models
from open_webui.models.customization import Connection, ConnectionMigration, ModelBinding
from open_webui.utils.oi import catalog
from open_webui.utils.oi.storage import identifier, now, insert_once

PREFIXES = ('openai.', 'ollama.', 'rag.', 'audio.', 'image_generation.', 'images.edit.')


def digest(value):
    return hmac.new(
        WEBUI_SECRET_KEY.encode(),
        json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode(),
        hashlib.sha256,
    ).hexdigest()


async def snapshot(db, user, scope):
    if scope == 'personal':
        row = await db.get(User, user.id, populate_existing=True)
        if row is None:
            raise HTTPException(404, 'User unavailable')
        return copy.deepcopy(((row.settings or {}).get('ui') or {}).get('directConnections') or {})
    if scope != 'server' or user.role != 'admin':
        raise HTTPException(403, 'Administrator required')
    if not Config.PERSISTENT_ENABLED:
        raise HTTPException(422, 'Persistent configuration is required for atomic migration')
    values = {k: copy.deepcopy(v) for k, v in Config.DEFAULTS.items() if k.startswith(PREFIXES)}
    for row in (await db.execute(select(Config))).scalars():
        if row.key.startswith(PREFIXES):
            values[row.key] = copy.deepcopy(row.value)
    return values


def source_entry(
    key, kind, index, url, api_key, config, *, transport='server', models=None, capability=None, config_updates=None
):
    protocol = (
        'ollama'
        if kind == 'ollama'
        else ('openai-responses' if config.get('api_type') == 'responses' else 'openai-chat')
    )
    settings = {'auth': 'api-key' if config.get('azure') else config.get('auth_type', 'bearer')}
    if config.get('api_version'):
        settings['api_version'] = config['api_version']
    settings.update(config.get('connection_settings') or {})
    if config.get('connection_type'):
        settings['connection_type'] = config['connection_type']
    params = copy.deepcopy(config.get('params') or {})
    extra = config.get('extra_params') or {}
    if not isinstance(extra, dict):
        raise ValueError('Extra parameters must be an object')
    if extra:
        params['provider_options'] = {**extra, **params.get('provider_options', {})}
    if not isinstance(params, dict):
        raise ValueError('Parameters must be an object')
    template = config.get('template')
    if not template:
        if kind == 'ollama':
            template = 'ollama'
        elif config.get('azure'):
            template = 'azure-openai'
        elif url.rstrip('/') in {'https://openrouter.ai/api/v1', 'https://openrouter.ai/api'}:
            template = 'openrouter'
        else:
            template = 'openai-compatible'
    source = {
        'id': key,
        'kind': kind,
        'index': index,
        'transport': transport,
        'url': url.rstrip('/'),
        'protocol': config.get('protocol') or protocol,
        'template': template,
        'name': config.get('name') or config.get('prefix_id') or f'{urlsplit(url).hostname} · {key}',
        'credentials': {'api_key': api_key or '', 'headers': copy.deepcopy(config.get('headers') or {})},
        'settings': settings,
        'params': params,
        'enabled': config.get('enable', True),
        'prefix': config.get('prefix_id') or '',
        'models': models if models is not None else (config.get('model_ids') or []),
        'capability': capability,
        'config_updates': config_updates or {},
        'credential_keys': [],
        'model_capabilities': {},
        'issue': None,
    }
    try:
        catalog.validate_url(source['url'])
    except ValueError:
        source['issue'] = 'invalid_legacy_url'
    source['origin'] = digest(
        {k: source[k] for k in ('kind', 'transport', 'url', 'protocol', 'prefix', 'credentials', 'settings')}
    )
    return source


def sources(state, scope):
    result = []
    for kind in ['openai'] if scope == 'personal' else ['openai', 'ollama']:
        urls = (
            state.get('OPENAI_API_BASE_URLS', [])
            if scope == 'personal'
            else state.get(f'{kind}.api_base_urls' if kind == 'openai' else 'ollama.base_urls', [])
        )
        keys = state.get('OPENAI_API_KEYS', []) if scope == 'personal' else state.get('openai.api_keys', [])
        configs = state.get('OPENAI_API_CONFIGS', {}) if scope == 'personal' else state.get(f'{kind}.api_configs', {})
        for index, url in enumerate(urls):
            config = copy.deepcopy(configs.get(str(index), configs.get(url, {})))
            if config.get('oi_connection_id'):
                continue
            if scope == 'server':
                config['enable'] = bool(state.get(f'{kind}.enable', True) and config.get('enable', True))
            result.append(
                source_entry(
                    f'{kind}:{index}',
                    kind,
                    index,
                    url,
                    keys[index] if kind == 'openai' and index < len(keys) else config.get('key', ''),
                    config,
                    transport='browser' if scope == 'personal' else 'server',
                )
            )
    if scope == 'server':
        embedding = state.get('rag.embedding_engine', '')
        if embedding in {'openai', 'ollama', 'azure_openai'}:
            prefix = f'rag.{embedding}'
            result.append(
                source_entry(
                    'embedding',
                    'ollama' if embedding == 'ollama' else 'aux',
                    None,
                    state.get(prefix + ('.base_url' if embedding != 'openai' else '.api_base_url')) or '',
                    state.get(prefix + '.api_key'),
                    {
                        'name': 'Embedding',
                        'azure': embedding == 'azure_openai',
                        'api_version': state.get(prefix + '.api_version'),
                    },
                    models=[state.get('rag.embedding_model')],
                    capability='embedding',
                    config_updates={
                        'rag.embedding_engine': 'connection',
                        'rag.embedding_model': '$model',
                        prefix + '.api_key': '',
                    },
                )
            )
        if state.get('rag.reranking_engine') == 'external':
            url = state.get('rag.external_reranker_url') or ''
            base, path = url.rsplit('/', 1) if '/' in url else (url, 'rerank')
            result.append(
                source_entry(
                    'rerank',
                    'aux',
                    None,
                    base,
                    state.get('rag.external_reranker_api_key'),
                    {'name': '重排', 'connection_settings': {'rerank_path': '/' + path}},
                    models=[state.get('rag.reranking_model')],
                    capability='rerank',
                    config_updates={
                        'rag.reranking_engine': 'connection',
                        'rag.reranking_model': '$model',
                        'rag.external_reranker_api_key': '',
                    },
                )
            )
        for prefix, capability in [
            ('audio.tts', 'tts'),
            ('audio.stt', 'stt'),
            ('image_generation', 'image_generation'),
            ('images.edit', 'image_edit'),
        ]:
            engine = state.get(prefix + '.engine', '')
            if engine in {'openai', 'gemini'}:
                options = copy.deepcopy(state.get(prefix + '.openai.params') or {})
                if engine == 'gemini':
                    options['endpoint_method'] = state.get(prefix + '.gemini.endpoint_method') or 'generateContent'
                version = state.get(prefix + '.openai.api_version')
                item = source_entry(
                    capability,
                    'aux',
                    None,
                    state.get(prefix + f'.{engine}.api_base_url') or '',
                    state.get(prefix + f'.{engine}.api_key'),
                    {
                        'name': capability,
                        'protocol': 'gemini' if engine == 'gemini' else 'openai-chat',
                        'template': (
                            'gemini' if engine == 'gemini' else 'azure-openai' if version else 'openai-compatible'
                        ),
                        'azure': bool(version),
                        'api_version': version,
                        'params': {'capability_params': {capability: options}},
                    },
                    models=[state.get(prefix + '.model')],
                    capability=capability,
                    config_updates={
                        prefix + '.engine': 'connection',
                        prefix + '.model': '$model',
                        prefix + f'.{engine}.api_key': '',
                    },
                )
                if capability == 'stt' and state.get('audio.stt.openai.api_request_format', 'multipart') not in {
                    'multipart',
                    'form-data',
                    None,
                    '',
                }:
                    item['issue'] = 'unsupported_transcription_request_format'
                result.append(item)
            elif engine and engine not in {'connection', 'web'}:
                result.append(
                    {
                        'id': capability,
                        'kind': 'compatibility',
                        'name': capability,
                        'transport': 'server',
                        'url': '',
                        'protocol': engine,
                        'models': [],
                        'origin': None,
                        'issue': 'retain_existing_engine',
                        'config_updates': {},
                        'enabled': True,
                    }
                )
        provider = state.get('audio.realtime.provider', '')
        if (
            state.get('audio.realtime.enabled')
            and provider == 'openai'
            and state.get('audio.realtime.auth_type', 'api_key') == 'api_key'
        ):
            voice = state.get('audio.realtime.model')
            transcription = state.get('audio.realtime.transcription_model')
            item = source_entry(
                'realtime',
                'aux',
                None,
                state.get('audio.realtime.openai.api_base_url') or '',
                state.get('audio.realtime.openai.api_key'),
                {'name': 'Realtime'},
                models=list(dict.fromkeys(m for m in [voice, transcription] if m)),
                capability='realtime',
                config_updates={
                    'audio.realtime.provider': 'connection',
                    'audio.realtime.model': '$model:' + str(voice),
                    'audio.realtime.transcription_model': '$model:' + str(transcription),
                    'audio.realtime.openai.api_key': '',
                },
            )
            item['model_capabilities'] = {voice: {'realtime': True}}
            item['model_capabilities'].setdefault(transcription, {})['stt'] = True
            if not voice or not transcription:
                item['issue'] = 'missing_realtime_model'
            result.append(item)
        elif state.get('audio.realtime.enabled') and provider not in {'', 'connection'}:
            result.append(
                {
                    'id': 'realtime',
                    'kind': 'compatibility',
                    'name': 'Realtime',
                    'transport': 'server',
                    'url': '',
                    'protocol': provider,
                    'models': [],
                    'origin': None,
                    'issue': 'retain_existing_engine',
                    'config_updates': {},
                    'enabled': True,
                }
            )
    duplicates = Counter(item['origin'] for item in result if item.get('origin'))
    for item in result:
        item['ambiguous'] = bool(item.get('origin') and duplicates[item['origin']] > 1)
    return result


async def preview(db, user, scope):
    state = await snapshot(db, user, scope)
    try:
        items = sources(state, scope)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, 'Legacy configuration is malformed; repair it before migration') from None
    entries = []
    for item in items:
        legacy_key = (f'browser:{user.id}:' if scope == 'personal' else 'server:') + str(item.get('origin'))
        existing = (
            await db.execute(select(Connection).where(Connection.legacy_key == legacy_key))
        ).scalar_one_or_none()
        # Phase-one imports used an array index. Only offer them as an explicit candidate, never guess.
        old = None
        if scope == 'server' and item['kind'] in {'openai', 'ollama'} and item['index'] is not None:
            old = (await db.execute(select(Connection).where(Connection.legacy_key == item['id']))).scalar_one_or_none()
        entries.append(
            {
                'id': item['id'],
                'name': item['name'],
                'base_url': '' if item['issue'] == 'invalid_legacy_url' else item['url'],
                'protocol': item['protocol'],
                'transport': item['transport'],
                'models': [m for m in item['models'] if m],
                'enabled': item['enabled'],
                'issue': item['issue'],
                'ambiguous': item['ambiguous'],
                'existing_connection': catalog.connection_json(existing or old, user) if existing or old else None,
                'suggested_action': (
                    'reuse' if existing else 'review' if old or item['ambiguous'] or item['issue'] else 'create'
                ),
            }
        )
    return {'scope': scope, 'version': digest({'user': user.id, 'scope': scope, 'state': state}), 'entries': entries}


async def apply(db, user, scope, version, choices):
    receipt_id = f'migration:{user.id}:{scope}:{version}'
    receipt = await db.get(ConnectionMigration, receipt_id)
    if receipt:
        if receipt.result.get('choices') != choices:
            raise HTTPException(409, 'This preview was already applied with different choices')
        return receipt.result
    lock_id = 'lock:' + ('server' if scope == 'server' else user.id)
    await insert_once(
        db,
        ConnectionMigration,
        {'id': lock_id, 'user_id': user.id, 'scope': 'lock', 'result': {}, 'created_at': now()},
        ['id'],
    )
    await db.execute(
        update(ConnectionMigration)
        .where(ConnectionMigration.id == lock_id)
        .values(created_at=ConnectionMigration.created_at)
    )
    if scope == 'personal':
        await db.execute(update(User).where(User.id == user.id).values(updated_at=User.updated_at))
    else:
        await db.execute(select(Config).order_by(Config.key).with_for_update())
    receipt = await db.get(ConnectionMigration, receipt_id, populate_existing=True)
    if receipt:
        if receipt.result.get('choices') != choices:
            raise HTTPException(409, 'This preview was already applied with different choices')
        return receipt.result
    state = await snapshot(db, user, scope)
    if not hmac.compare_digest(version, digest({'user': user.id, 'scope': scope, 'state': state})):
        raise HTTPException(409, 'Legacy configuration changed; refresh the migration preview')
    items = {item['id']: item for item in sources(state, scope)}
    if not choices or len({choice['source_id'] for choice in choices}) != len(choices):
        raise HTTPException(422, 'Select distinct sources to migrate')
    prepared = []
    for choice in choices:
        item = items.get(choice['source_id'])
        if not item or item['issue']:
            raise HTTPException(422, 'A selected source requires manual configuration')
        if item['ambiguous'] and not choice.get('acknowledge_duplicate'):
            raise HTTPException(409, 'Confirm duplicate sources independently')
        existing = await db.get(Connection, choice.get('connection_id')) if choice.get('connection_id') else None
        if choice['action'] == 'reuse':
            if (
                existing is None
                or not catalog.editable(existing, user)
                or (existing.transport or 'server') != item['transport']
            ):
                raise HTTPException(403, 'Selected target connection is unavailable')
            if (existing.settings or {}).get('deleted') or (item['enabled'] and not existing.enabled):
                raise HTTPException(409, 'The target connection is removed or disabled')
            if scope == 'server' and not existing.shared:
                raise HTTPException(
                    409,
                    'Global configuration requires a shared server connection; private access is not expanded automatically',
                )
            if canonical_protocol(existing.protocol) != canonical_protocol(item['protocol']):
                raise HTTPException(409, 'Target protocol does not match the source')
            models = [{'id': m} for m in item['models'] if isinstance(m, str) and m]
            if not models and item['transport'] == 'server' and item['enabled']:
                raise HTTPException(409, 'Discover and confirm source models before reusing this connection')
            prepared.append((choice, item, existing, models))
            continue
        if choice['action'] != 'create':
            raise HTTPException(422, 'Choose create or reuse')
        origin = item['origin'] + (':' + item['id'] if item['ambiguous'] else '')
        legacy_key = (f'browser:{user.id}:' if scope == 'personal' else 'server:') + origin
        existing = (
            await db.execute(select(Connection).where(Connection.legacy_key == legacy_key))
        ).scalar_one_or_none()
        if existing:
            models = [{'id': m} for m in item['models'] if isinstance(m, str) and m]
            if not models and item['transport'] == 'server' and item['enabled']:
                raise HTTPException(409, 'Discover and confirm source models before reusing this connection')
            prepared.append((choice, item, existing, models))
            continue
        target = Connection(
            id=identifier(),
            owner_id=user.id,
            name=item['name'],
            template=item['template'],
            protocol=item['protocol'],
            transport=item['transport'],
            base_url=catalog.validate_url(item['url']),
            credentials=catalog.encrypt_credentials(item['credentials']),
            settings={**item['settings'], 'migration_origin': origin},
            shared=scope == 'server',
            enabled=item['enabled'],
            legacy_key=legacy_key,
            revision=1,
            created_at=now(),
            updated_at=now(),
        )
        target.base_url, target.settings = normalize_service_address(
            target.base_url, target.settings, target.protocol, target.template, legacy=True
        )
        target.protocol = canonical_protocol(target.protocol)
        models = [{'id': m} for m in item['models'] if isinstance(m, str) and m]
        if not models and item['transport'] == 'server' and item['enabled']:
            try:
                models = await catalog.fetch_models(target)
            except Exception:
                raise HTTPException(502, 'Model discovery failed; migration was not applied') from None
        prepared.append((choice, item, target, models))
    result = {
        'scope': scope,
        'choices': choices,
        'connections': [],
        'model_mapping': {},
        'retained_sources': list(set(items) - {c['source_id'] for c in choices}),
    }
    config_updates = {}
    direct = copy.deepcopy(state) if scope == 'personal' else None
    for choice, item, target, models in prepared:
        db.add(target)
        await db.flush()
        seen_models = set()
        for model in models:
            raw = model.get('id') or model.get('model') or model.get('name')
            if not isinstance(raw, str) or not raw:
                raise HTTPException(422, 'Invalid legacy model ID')
            upstream = (model.get('openai') or {}).get('id') or raw
            if item['prefix'] and upstream.startswith(item['prefix'] + '.'):
                upstream = upstream[len(item['prefix']) + 1 :]
            if upstream in seen_models:
                continue
            seen_models.add(upstream)
            alias = item['prefix'] + '.' + upstream if item['prefix'] else raw
            legacy = (
                await Models.get_model_by_id(alias, db=db) if scope == 'server' and not item['capability'] else None
            )
            caps = item.get('model_capabilities', {}).get(upstream) or (
                {item['capability']: True} if item['capability'] else catalog.discovered_capabilities(model)
            )
            params = copy.deepcopy(item['params'])
            if legacy:
                overrides = legacy.params.model_dump(exclude_unset=True)
                for nested in ('provider_options', 'custom_params'):
                    if isinstance(params.get(nested), dict) and isinstance(overrides.get(nested), dict):
                        overrides[nested] = {**params[nested], **overrides[nested]}
                params.update(overrides)
            if any(
                isinstance(params.get(key), dict) and params[key].get('model', upstream) != upstream
                for key in ('provider_options', 'custom_params')
            ):
                raise HTTPException(409, 'A legacy parameter overrides the model identity; resolve it explicitly')
            matches = (
                (
                    await db.execute(
                        select(ModelBinding).where(
                            ModelBinding.connection_id == target.id, ModelBinding.upstream_id == upstream
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(matches) > 1:
                raise HTTPException(409, 'Target model mapping is ambiguous; no changes applied')
            if matches:
                binding = matches[0]
                if catalog.effective_protocol(binding, target) != canonical_protocol(target.protocol):
                    raise HTTPException(409, 'Target model protocol differs; resolve the mapping explicitly')
                if scope == 'server' and not item['capability']:
                    prior_acl = (binding.capabilities or {}).get('legacy_model_id')
                    if prior_acl and prior_acl != alias:
                        raise HTTPException(409, 'Target permission provenance differs; resolve explicitly')
                    binding.capabilities = {
                        **(binding.capabilities or {}),
                        'legacy_model_id': alias,
                        'legacy_unregistered': not bool(legacy),
                    }
                binding.aliases = list(dict.fromkeys([*(binding.aliases or []), alias]))
                continue
            binding = ModelBinding(
                id='oi:' + identifier(),
                connection_id=target.id,
                upstream_id=upstream,
                protocol=target.protocol,
                name=legacy.name if legacy else model.get('name') or upstream,
                enabled=legacy.is_active if legacy else item['enabled'],
                available=True,
                capabilities={
                    'discovered': caps,
                    'overrides': {},
                    'manual': True,
                    **(
                        {'legacy_model_id': alias, 'legacy_unregistered': not bool(legacy)}
                        if scope == 'server' and not item['capability']
                        else {}
                    ),
                },
                pricing={},
                params=params,
                aliases=[alias],
                updated_at=now(),
            )
            db.add(binding)
        await db.flush()
        bindings = (
            (await db.execute(select(ModelBinding).where(ModelBinding.connection_id == target.id))).scalars().all()
        )
        for binding in bindings:
            for alias in binding.aliases or []:
                result['model_mapping'].setdefault(alias, []).append(binding.id)
        if item['config_updates']:
            for key, value in item['config_updates'].items():
                if isinstance(value, str) and value.startswith('$model'):
                    raw = value[len('$model:') :] if value.startswith('$model:') else item['models'][0]
                    matches = [b for b in bindings if b.upstream_id == raw]
                    if len(matches) != 1:
                        raise HTTPException(409, 'Auxiliary model mapping is not unique; no changes applied')
                    required = item.get('model_capabilities', {}).get(raw) or {item['capability']: True}
                    if any(catalog.capabilities(matches[0]).get(cap) is not True for cap in required):
                        raise HTTPException(409, 'Confirm the target model capability before reusing it')
                    config_updates[key] = matches[0].id
                else:
                    config_updates[key] = value
        if item['kind'] in {'openai', 'ollama'} and item['index'] is not None:
            if scope == 'personal':
                direct.setdefault('OPENAI_API_CONFIGS', {})[str(item['index'])] = {
                    **direct.get('OPENAI_API_CONFIGS', {}).get(str(item['index']), {}),
                    'oi_connection_id': target.id,
                    'enable': False,
                }
                if item['index'] < len(direct.get('OPENAI_API_KEYS', [])):
                    direct['OPENAI_API_KEYS'][item['index']] = ''
                direct['OPENAI_API_CONFIGS'][str(item['index'])].pop('headers', None)
                direct['OPENAI_API_CONFIGS'][str(item['index'])].pop('key', None)
            else:
                key = f'{item["kind"]}.api_configs'
                configs = copy.deepcopy(config_updates.get(key, state.get(key, {})))
                original = configs.get(str(item['index']), configs.get(item['url'], {}))
                configs[str(item['index'])] = {**original, 'oi_connection_id': target.id, 'enable': False}
                configs[str(item['index'])].pop('headers', None)
                configs[str(item['index'])].pop('key', None)
                config_updates[key] = configs
                if item['kind'] == 'openai':
                    keys = copy.deepcopy(config_updates.get('openai.api_keys', state.get('openai.api_keys', [])))
                    if item['index'] < len(keys):
                        keys[item['index']] = ''
                    config_updates['openai.api_keys'] = keys
        result['connections'].append(
            {'source_id': item['id'], 'connection_id': target.id, 'binding_ids': [binding.id for binding in bindings]}
        )
    # URL-keyed legacy configurations may coexist with index-keyed overrides. Scrub a
    # URL entry only when every array slot using it was selected, preserving partial migration.
    for kind in ['openai'] if scope == 'personal' else ['openai', 'ollama']:
        urls = (
            state.get('OPENAI_API_BASE_URLS', [])
            if scope == 'personal'
            else state.get('openai.api_base_urls' if kind == 'openai' else 'ollama.base_urls', [])
        )
        config_key = 'OPENAI_API_CONFIGS' if scope == 'personal' else kind + '.api_configs'
        configs = direct.get(config_key, {}) if scope == 'personal' else config_updates.get(config_key, {})
        for url in set(urls):
            if url in configs and all(
                configs.get(str(i), {}).get('oi_connection_id') for i, value in enumerate(urls) if value == url
            ):
                configs[url].pop('headers', None)
                configs[url].pop('key', None)
    if scope == 'personal':
        row = await db.get(User, user.id, populate_existing=True)
        if not result['retained_sources']:
            direct = {
                'version': 2,
                'connection_ids': list(
                    dict.fromkeys(
                        c.get('oi_connection_id')
                        for c in direct.get('OPENAI_API_CONFIGS', {}).values()
                        if c.get('oi_connection_id')
                    )
                ),
            }
        settings = copy.deepcopy(row.settings or {})
        settings.setdefault('ui', {})['directConnections'] = direct
        row.settings = settings
    else:
        await Config.upsert(config_updates, db=db)
    result['model_mapping'] = {alias: list(dict.fromkeys(ids)) for alias, ids in result['model_mapping'].items()}
    db.add(ConnectionMigration(id=receipt_id, user_id=user.id, scope=scope, result=result, created_at=now()))
    await db.flush()
    return result


def public_user_settings(settings):
    settings = copy.deepcopy(settings.model_dump() if hasattr(settings, 'model_dump') else settings or {})
    direct = (settings.get('ui') or {}).get('directConnections')
    if isinstance(direct, dict) and direct.get('version') == 2:
        settings['ui']['directConnections'] = {'version': 2, 'connection_ids': direct.get('connection_ids', [])}
    if isinstance(direct, dict) and direct.get('version') != 2:
        configs = direct.get('OPENAI_API_CONFIGS') or {}
        settings['ui']['directConnections'] = {
            'legacy_pending': True,
            'count': len(direct.get('OPENAI_API_BASE_URLS') or []),
            'connection_ids': [
                item['oi_connection_id']
                for item in configs.values()
                if isinstance(item, dict) and item.get('oi_connection_id')
            ],
        }
    return settings
