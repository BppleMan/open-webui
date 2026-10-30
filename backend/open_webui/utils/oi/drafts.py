from open_webui.models.customization import ChatDraft
from open_webui.utils.oi.storage import insert_once, now
from sqlalchemy import select, update


class DraftConflict(Exception):
    def __init__(self, current):
        self.current = current


def serialize(row):
    return {
        'chat_id': row.chat_id,
        'revision': row.revision,
        'payload': row.payload,
        'mutation_id': row.mutation_id,
        'updated_at': row.updated_at,
    }


async def get_draft(db, user_id, chat_id):
    row = await db.get(ChatDraft, (user_id, chat_id), populate_existing=True)
    return (
        serialize(row)
        if row
        else {'chat_id': chat_id, 'revision': 0, 'payload': {}, 'mutation_id': None, 'updated_at': 0}
    )


async def put_draft(db, user_id, chat_id, payload, expected_revision, mutation_id):
    current = await get_draft(db, user_id, chat_id)
    if mutation_id and current['mutation_id'] == mutation_id:
        return current
    if current['revision'] != expected_revision:
        raise DraftConflict(current)
    stamp = now()
    if expected_revision == 0:
        changed = await insert_once(
            db,
            ChatDraft,
            {
                'user_id': user_id,
                'chat_id': chat_id,
                'revision': 1,
                'payload': payload,
                'mutation_id': mutation_id,
                'updated_at': stamp,
            },
            ['user_id', 'chat_id'],
        )
    else:
        result = await db.execute(
            update(ChatDraft)
            .where(
                ChatDraft.user_id == user_id,
                ChatDraft.chat_id == chat_id,
                ChatDraft.revision == expected_revision,
            )
            .values(payload=payload, mutation_id=mutation_id, revision=expected_revision + 1, updated_at=stamp)
            .returning(ChatDraft.revision)
        )
        changed = result.scalar_one_or_none() is not None
    if not changed:
        raise DraftConflict(await get_draft(db, user_id, chat_id))
    await db.flush()
    return await get_draft(db, user_id, chat_id)


async def clear_draft(db, user_id, chat_id, expected_revision, mutation_id):
    # Retain a tombstone revision: a delayed writer cannot resurrect a cleared draft.
    return await put_draft(db, user_id, chat_id, {}, expected_revision, mutation_id)


async def acknowledge_input(db, user_id, chat_id, expected_revision, mutation_id, input_version=None):
    for _ in range(3):
        current = await get_draft(db, user_id, chat_id)
        if current['mutation_id'] == mutation_id or (
            input_version and current['payload'].get('_oi_input_version') != input_version
        ):
            return {**current, 'previous_revision': current['revision'], 'applied': False}
        queue = current['payload'].get('queue') or []
        payload = {'queue': queue} if queue else {}
        try:
            result = await put_draft(
                db, user_id, chat_id, payload, current['revision'] if input_version else expected_revision, mutation_id
            )
            return {**result, 'previous_revision': current['revision'], 'applied': True}
        except DraftConflict:
            if not input_version:
                raise
    raise DraftConflict(await get_draft(db, user_id, chat_id))


async def acknowledge_queued(db, user_id, chat_id, queue_id, request_id, mutation_id):
    """Remove an accepted immutable queue version while preserving other input."""
    for _ in range(3):
        current = await get_draft(db, user_id, chat_id)
        queue = current['payload'].get('queue') or []
        if not isinstance(queue, list) or any(not isinstance(item, dict) for item in queue):
            raise DraftConflict(current)
        entry = next((item for item in queue if item.get('id') == queue_id), None)
        if entry is None:
            return {**current, 'previous_revision': current['revision'], 'applied': False}
        if entry.get('requestId') != request_id:
            raise DraftConflict(current)
        payload = {**current['payload'], 'queue': [item for item in queue if item.get('id') != queue_id]}
        try:
            saved = await put_draft(db, user_id, chat_id, payload, current['revision'], mutation_id)
            return {**saved, 'previous_revision': current['revision'], 'applied': True}
        except DraftConflict:
            continue
    raise DraftConflict(await get_draft(db, user_id, chat_id))


async def list_drafts(db, user_id):
    from open_webui.models.chats import Chat

    rows = (
        await db.execute(
            select(ChatDraft)
            .join(Chat, Chat.id == ChatDraft.chat_id)
            .where(ChatDraft.user_id == user_id, Chat.user_id == user_id)
            .order_by(ChatDraft.updated_at.desc())
        )
    ).scalars()
    return [
        serialize(row)
        for row in rows
        if row.payload.get('prompt') or row.payload.get('files') or row.payload.get('queue')
    ]
