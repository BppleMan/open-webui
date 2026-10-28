"""Atomic ordinary JSON imports; history cost is a replaceable baseline, not new calls."""

import hashlib
import json
from fastapi import HTTPException
from open_webui.models.chats import Chat, Chats, ChatModel
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.customization import ImportReceipt
from open_webui.utils.access_control.folders import has_folder_write_access
from open_webui.utils.oi import accounting, archive
from open_webui.utils.oi.storage import identifier, now, lock_user_state


async def import_json(db, user_id, forms, import_id=None):
    await Chats.require_chat_creation_permission(user_id, db=db)
    if not 1 <= len(forms) <= 1000:
        raise ValueError('Import between 1 and 1000 conversations at a time')
    encoded = json.dumps(
        [f.model_dump(exclude={'oi_cost_baseline'}) for f in forms], sort_keys=True, ensure_ascii=False
    )
    if len(encoded.encode()) > 64 * 1024 * 1024:
        raise ValueError('JSON import exceeds 64 MiB')
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    # Non-file callers also use this endpoint to make deliberate copies. They omit the receipt key.
    import_id = import_id or identifier()
    await lock_user_state(db, user_id)
    receipt = await db.get(ImportReceipt, (user_id, import_id), populate_existing=True)
    if receipt:
        if receipt.digest != digest:
            raise HTTPException(409, 'Import key was already used for different content')
        chats = [await db.get(Chat, cid) for cid in receipt.chat_ids]
        if any(chat is None or chat.user_id != user_id for chat in chats):
            raise HTTPException(409, 'A previously imported conversation was removed; choose a new import batch')
        return [ChatModel.model_validate(chat) for chat in chats]
    result = []
    for original in forms:
        form = original.model_copy(deep=True)
        if form.folder_id and not await has_folder_write_access(user_id, form.folder_id, db=db):
            form.folder_id = None
        body = form.chat
        body.pop('share_id', None)
        body.pop('share_mode', None)
        history = body.get('history') if isinstance(body.get('history'), dict) else {}
        messages = history.get('messages')
        if not isinstance(messages, dict):
            messages = {}
        if not messages and isinstance(body.get('messages'), list):
            parent = None
            for message in body['messages']:
                if not isinstance(message, dict):
                    raise ValueError('Invalid imported message')
                mid = message.get('id') or identifier()
                message = {**message, 'id': mid, 'parentId': message.get('parentId', parent)}
                messages[mid] = message
                parent = mid
            history['currentId'] = parent
        for mid, message in messages.items():
            if not isinstance(message, dict):
                raise ValueError('Invalid imported message')
            message.update(id=mid, user_id=user_id, childrenIds=[])
            message.pop('user', None)
            message.pop('feedbackId', None)
        for mid, message in messages.items():
            parent = message.get('parentId')
            if parent in messages:
                messages[parent]['childrenIds'].append(mid)
        history['messages'] = messages
        history.setdefault('currentId', form.current_message_id or next(reversed(messages), None))
        body['history'] = history
        body['messages'] = []
        archive.validate({'schema': archive.SCHEMA, 'root_chat_id': 'json', 'chat': body})
        cost = form.oi_cost_baseline or {}
        if not isinstance(cost, dict) or any(k not in {'actual_usd', 'estimated_usd', 'note'} for k in cost):
            raise ValueError('Invalid history cost baseline')
        actual, estimated = accounting.money(cost.get('actual_usd')), accounting.money(cost.get('estimated_usd'))
        if actual is not None and estimated is not None:
            raise ValueError('Choose actual or estimated historical cost')
        note = cost.get('note') or (
            'Imported history; cost supplied by owner'
            if actual is not None or estimated is not None
            else 'Imported history; cost has not been supplied'
        )
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError('Invalid cost note')
        model = Chats._chat_import_form_to_chat_model(user_id, form)
        model.chat['id'] = model.id
        chat = Chat(**model.model_dump())
        db.add(chat)
        await db.flush()
        db.add_all(
            [
                ChatMessages._build_message(f'{chat.id}-{mid}', chat.id, user_id, message, now() // 1000)
                for mid, message in messages.items()
            ]
        )
        if messages:
            await accounting.set_baseline(
                db, user_id, chat.id, 'imported-history', actual_usd=actual, estimated_usd=estimated, note=note
            )
        result.append(chat)
    db.add(
        ImportReceipt(
            user_id=user_id, import_id=import_id, digest=digest, chat_ids=[c.id for c in result], created_at=now()
        )
    )
    await db.flush()
    return [ChatModel.model_validate(chat) for chat in result]
