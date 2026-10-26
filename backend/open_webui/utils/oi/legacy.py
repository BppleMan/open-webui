"""Explicit, idempotent import of existing server connections; no guessed duplicate routing."""

from urllib.parse import urlparse

from open_webui.models.customization import Connection, ModelBinding
from open_webui.models.models import Models
from open_webui.utils.oi import catalog
from open_webui.utils.oi.storage import identifier, insert_once, now
from sqlalchemy import select


async def import_connections(db, request, user):
    from open_webui.routers import ollama, openai

    enabled, urls, keys, configs = await openai.get_openai_runtime_config()
    responses = await openai.get_all_models_responses(request, user) if enabled else []
    sources = []
    for index, url in enumerate(urls):
        config = configs.get(str(index), configs.get(url, {}))
        raw = responses[index] if index < len(responses) else None
        sources.append(('openai', index, url, keys[index] if index < len(keys) else '', config, raw))
    enabled, urls, configs = await ollama.get_ollama_runtime_config()
    for index, url in enumerate(urls):
        config = configs.get(str(index), configs.get(url, {}))
        raw = (
            await ollama.send_get_request(request, f'{url}/api/tags', config.get('key'), user=user, config=config)
            if enabled and config.get('enable', True)
            else None
        )
        sources.append(('ollama', index, url, config.get('key', ''), config, raw))
    result = []
    for kind, index, url, key, config, raw in sources:
        legacy_key = f'{kind}:{index}'
        connection = (
            await db.execute(select(Connection).where(Connection.legacy_key == legacy_key))
        ).scalar_one_or_none()
        protocol = (
            'ollama'
            if kind == 'ollama'
            else 'openai-responses'
            if config.get('api_type') == 'responses'
            else 'openai-chat'
        )
        if connection is None:
            identity = identifier()
            await insert_once(
                db,
                Connection,
                dict(
                    id=identity,
                    owner_id=user.id,
                    name=config.get('name') or config.get('prefix_id') or f'{urlparse(url).hostname} · {index + 1}',
                    template='ollama'
                    if kind == 'ollama'
                    else 'azure-openai'
                    if config.get('azure')
                    else 'openrouter'
                    if urlparse(url).hostname == 'openrouter.ai'
                    else 'openai-compatible',
                    protocol=protocol,
                    base_url=catalog.validate_url(url),
                    credentials=catalog.encrypt_credentials({'api_key': key, 'headers': config.get('headers', {})}),
                    settings={
                        'legacy_source': legacy_key,
                        'api_version': config.get('api_version'),
                        'auth': 'api-key' if config.get('azure') else config.get('auth_type', 'bearer'),
                    },
                    shared=True,
                    enabled=config.get('enable', True),
                    legacy_key=legacy_key,
                    revision=1,
                    created_at=now(),
                    updated_at=now(),
                ),
                ['legacy_key'],
            )
            connection = (await db.execute(select(Connection).where(Connection.legacy_key == legacy_key))).scalar_one()
        model_list = raw if isinstance(raw, list) else (raw or {}).get('models' if kind == 'ollama' else 'data', [])
        prefix = config.get('prefix_id')
        for item in model_list:
            legacy_id = item.get('id') or item.get('model') or item.get('name')
            upstream = (item.get('openai') or {}).get('id') or legacy_id
            if prefix and upstream.startswith(prefix + '.'):
                upstream = upstream[len(prefix) + 1 :]
            if kind == 'ollama' and prefix and not legacy_id.startswith(prefix + '.'):
                legacy_id = prefix + '.' + legacy_id
            legacy = await Models.get_model_by_id(legacy_id, db=db)
            meta = legacy.meta.model_dump() if legacy and legacy.meta else {}
            existing = (
                await db.execute(
                    select(ModelBinding).where(
                        ModelBinding.connection_id == connection.id,
                        ModelBinding.upstream_id == upstream,
                        ModelBinding.protocol == protocol,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue  # Refresh never resets local overrides, names, availability or prices.
            await insert_once(
                db,
                ModelBinding,
                dict(
                    id='oi:' + identifier(),
                    connection_id=connection.id,
                    upstream_id=upstream,
                    protocol=protocol,
                    name=legacy.name if legacy else item.get('name') or upstream,
                    enabled=legacy.is_active if legacy else True,
                    available=True,
                    capabilities={
                        'discovered': meta.get('capabilities', {}),
                        'overrides': {},
                        'legacy_model_id': legacy_id,
                        'legacy_unregistered': not bool(legacy),
                    },
                    pricing={},
                    params=legacy.params.model_dump() if legacy else {},
                    aliases=[legacy_id],
                    updated_at=now(),
                ),
                ['connection_id', 'upstream_id', 'protocol'],
            )
        result.append(
            {
                'connection_id': connection.id,
                'legacy_source': legacy_key,
                'discovered': len(model_list),
                'discovery_failed': raw is None,
            }
        )
    await db.flush()
    return result


async def alias_map(db, user):
    pairs = (
        await db.execute(select(ModelBinding, Connection).join(Connection, ModelBinding.connection_id == Connection.id))
    ).all()
    result = {}
    for binding, connection in pairs:
        if catalog.identity_visible(connection, user):
            for alias in binding.aliases or []:
                result.setdefault(alias, []).append(binding.id)
    return result
