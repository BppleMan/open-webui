"""Authenticated, request-local native context transfer; never writes temporary bodies."""

from copy import deepcopy

from fastapi import HTTPException
from open_webui.utils.oi.temporary_native import digest, projection, restore, seal


def transfer_capsule(source, destination, user_id, source_temporary_id, destination_temporary_id):
    if not isinstance(destination.get('id'), str) or not destination['id']:
        raise HTTPException(422, 'A destination message is required')
    if source.get('role') != 'assistant' or destination.get('role') != 'assistant':
        raise HTTPException(422, 'Only assistant continuation state can be copied')
    source = deepcopy(source)
    calls = restore(source, user_id, source_temporary_id)
    unchanged = digest(projection(source)) == digest(projection(destination))
    return {
        'oi_native_state': seal(calls, projection(destination), user_id, destination_temporary_id, destination['id'])
        if unchanged and calls
        else None,
        'oi_native_invalidated': not (unchanged and bool(calls)),
    }


async def side_context(db, user, destination_temporary_id, messages, *, source_chat_id=None, source_temporary_id=None):
    from open_webui.models.chats import Chats
    from open_webui.utils.oi.native_privacy import project_native

    saved = {}
    if source_chat_id:
        chat = await Chats.get_accessible_chat_by_id(source_chat_id, user, db=db, permission='read')
        if chat is None:
            raise HTTPException(404, 'Source conversation unavailable')
        from open_webui.models.chat_messages import ChatMessages

        saved = await ChatMessages.get_messages_map_by_chat_id(source_chat_id, db=db)
        saved = deepcopy(saved or (chat.chat or {}).get('history', {}).get('messages', {}))
        body = {'history': {'messages': saved}}
        await project_native(body, chat.user_id, user.id, db, portable=True)
        saved = body['history']['messages']
    patches = []
    for message in messages:
        if message.get('role') != 'assistant':
            continue
        if source_temporary_id:
            patch = transfer_capsule(message, message, user.id, source_temporary_id, destination_temporary_id)
        else:
            original = saved.get(message.get('id')) or {}
            unchanged = (
                original.get('role') == 'assistant'
                and original.get('model') == message.get('model')
                and digest(projection(original)) == digest(projection(message))
            )
            calls = original.get('oi_native') if unchanged and not original.get('oi_native_invalidated') else []
            patch = {
                'oi_native_state': seal(calls, projection(message), user.id, destination_temporary_id, message['id'])
                if calls
                else None,
                'oi_native_invalidated': not bool(calls),
            }
        patches.append({'id': message['id'], **patch})
    return patches
