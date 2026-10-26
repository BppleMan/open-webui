"""Display-only identities from the caller's own calls and conversation snapshots."""

from sqlalchemy import select
from open_webui.models.customization import ModelCall, Generation, ModelBinding, Connection


async def identities(db, user):
    from open_webui.models.chats import Chat
    from open_webui.models.chat_messages import ChatMessage

    result = {}
    trusted_refs = set()
    refs = set(
        (
            await db.execute(
                select(ChatMessage.model_id)
                .where(ChatMessage.user_id == user.id, ChatMessage.model_id.isnot(None))
                .distinct()
            )
        ).scalars()
    )
    for call in (
        await db.execute(
            select(ModelCall)
            .join(Generation, Generation.id == ModelCall.generation_id)
            .where(Generation.user_id == user.id)
            .order_by(ModelCall.created_at)
        )
    ).scalars():
        if not (call.usage or {}).get('archive_origin'):
            trusted_refs.update(value for value in [call.model_id, call.binding_id] if value)
        refs.add(call.model_id)
        if call.binding_id:
            refs.add(call.binding_id)
        descriptor = (call.usage or {}).get('context', {}).get('model_identity')
        if isinstance(descriptor, dict):
            for key in [call.model_id, call.binding_id]:
                if key:
                    result.setdefault(key, {**descriptor, 'id': key})
    for metadata in (await db.execute(select(Chat.meta).where(Chat.user_id == user.id))).scalars():
        for item in (metadata or {}).get('oi_model_descriptors', []):
            if isinstance(item, dict) and item.get('id') in refs:
                result.setdefault(item['id'], item)
    for binding, connection in (
        await db.execute(
            select(ModelBinding, Connection)
            .join(Connection, ModelBinding.connection_id == Connection.id)
            .where(ModelBinding.id.in_(refs))
        )
    ).all():
        from open_webui.utils.oi.catalog import binding_readable

        if binding.id not in trusted_refs and not await binding_readable(db, binding, connection, user):
            continue
        result.setdefault(
            binding.id,
            {
                'id': binding.id,
                'name': binding.name,
                'upstream_id': binding.upstream_id,
                'connection': {
                    'id': connection.id,
                    'name': connection.name,
                    'protocol': binding.protocol,
                    'transport': connection.transport,
                },
            },
        )
    # A descriptor never grants availability, credentials, parameters or permission.
    return [safe_descriptor(value) for value in result.values()]


def safe_descriptor(value):
    out = {key: str(value[key])[:512] for key in ('id', 'name', 'upstream_id') if isinstance(value.get(key), str)}
    c = value.get('connection')
    if isinstance(c, dict):
        out['connection'] = {
            key: str(c[key])[:512] for key in ('id', 'name', 'protocol', 'transport') if isinstance(c.get(key), str)
        }
    return {**out, 'historical': True}


async def decorate_conversation(db, chat_id, content):
    """Attach safe display snapshots only after the caller's chat access was checked.

    Never query a guessed model ID: provenance must belong to this conversation's
    own/inherited ledger. Deep copying leaves stored text and request history intact.
    """
    from copy import deepcopy
    from open_webui.utils.oi.accounting import conversation_records

    result = deepcopy(content)
    calls, _ = await conversation_records(db, chat_id)
    descriptors = {}
    for call in sorted(calls.values(), key=lambda item: item.created_at):
        value = (call.usage or {}).get('context', {}).get('model_identity')
        if isinstance(value, dict):
            for key in [call.model_id, call.binding_id]:
                if key:
                    descriptors.setdefault(key, safe_descriptor({**value, 'id': key}))
    messages = list((result.get('history', {}).get('messages') or {}).values())
    messages.extend(result.get('messages') or [])
    for message in messages:
        if message.get('model') in descriptors:
            message['oi_model_identity'] = descriptors[message['model']]
        elif isinstance(message.get('oi_model_identity'), dict):
            message['oi_model_identity'] = safe_descriptor(message['oi_model_identity'])
    return result
