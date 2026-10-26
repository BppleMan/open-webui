"""Portable conversation records. Imported billing events retain one canonical identity."""

import copy
from types import SimpleNamespace
from open_webui.utils.oi.history import identities, safe_descriptor
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import Chat
from open_webui.models.customization import (
    ChatAnnotation,
    ChatLedger,
    CostSnapshot,
    Generation,
    ImportCostBaseline,
    MessageRevision,
    ModelCall,
)
from open_webui.utils.oi import accounting
from open_webui.utils.oi.storage import identifier, now
from sqlalchemy import select

SCHEMA = 'oi-conversation-archive-v1'
TABLES = {
    'ledgers': ChatLedger,
    'generations': Generation,
    'calls': ModelCall,
    'snapshots': CostSnapshot,
    'baselines': ImportCostBaseline,
}


def record(row):
    return {column.key: copy.deepcopy(getattr(row, column.key)) for column in row.__table__.columns}


def canonical_id(user_id, kind, source):
    return str(uuid5(NAMESPACE_URL, f'oi-archive:{user_id}:{kind}:{source}'))


async def export_records(db, user_id, chat_id, directory=None):
    chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        raise PermissionError('Only the owner can archive a conversation')
    body = copy.deepcopy(chat.chat or {})
    body.setdefault('history', {})['messages'] = await ChatMessages.get_messages_map_by_chat_id(
        chat_id, db=db
    ) or body.get('history', {}).get('messages', {})
    body.pop('share_id', None)
    body.pop('share_mode', None)
    messages = body['history']['messages']
    billing = {key: {} for key in TABLES}
    pending = []
    ledger = await db.get(ChatLedger, chat_id)
    if ledger:
        billing['ledgers'][chat_id] = record(ledger)
        pending.append((chat_id, ledger.sequence, ledger.inherited_snapshot_id))
    covered = {}
    while pending:
        source_chat, boundary, inherited = pending.pop()
        if source_chat in covered and covered[source_chat] >= boundary:
            continue
        covered[source_chat] = boundary
        source = await db.get(ChatLedger, source_chat)
        if source is None or source.user_id != user_id:
            raise PermissionError('Cost lineage is not owned by this user')
        billing['ledgers'][source_chat] = record(source)
        calls = (
            (
                await db.execute(
                    select(ModelCall).where(ModelCall.chat_id == source_chat, ModelCall.sequence <= boundary)
                )
            )
            .scalars()
            .all()
        )
        for call in calls:
            billing['calls'][call.id] = record(call)
            generation = await db.get(Generation, call.generation_id)
            if generation is None or generation.user_id != user_id:
                raise PermissionError('Call lineage is not owned by this user')
            billing['generations'][generation.id] = record(generation)
        snapshots = (
            (
                await db.execute(
                    select(CostSnapshot).where(
                        CostSnapshot.chat_id == source_chat, CostSnapshot.max_sequence <= boundary
                    )
                )
            )
            .scalars()
            .all()
        )
        for snapshot in snapshots:
            billing['snapshots'][snapshot.id] = record(snapshot)
            if snapshot.generation_id and snapshot.generation_id not in billing['generations']:
                generation = await db.get(Generation, snapshot.generation_id)
                if generation is not None:
                    billing['generations'][generation.id] = record(generation)
            for baseline_id in snapshot.baseline_ids:
                baseline = await db.get(ImportCostBaseline, baseline_id)
                if baseline is not None:
                    billing['baselines'][baseline.id] = record(baseline)
            if snapshot.inherited_snapshot_id:
                anchor = await db.get(CostSnapshot, snapshot.inherited_snapshot_id)
                if anchor is None:
                    raise ValueError('Missing cost anchor')
                pending.append((anchor.chat_id, anchor.max_sequence, anchor.inherited_snapshot_id))
                billing['snapshots'][anchor.id] = record(anchor)
        if inherited:
            anchor = await db.get(CostSnapshot, inherited)
            if anchor is None:
                raise ValueError('Missing inherited cost anchor')
            pending.append((anchor.chat_id, anchor.max_sequence, anchor.inherited_snapshot_id))
            billing['snapshots'][anchor.id] = record(anchor)
    # Current import baselines are meaningful even if no model has run yet.
    for baseline in (
        await db.execute(select(ImportCostBaseline).where(ImportCostBaseline.chat_id == chat_id))
    ).scalars():
        billing['baselines'][baseline.id] = record(baseline)
    for generation in (await db.execute(select(Generation).where(Generation.chat_id == chat_id))).scalars():
        billing['generations'][generation.id] = record(generation)
    revisions = (await db.execute(select(MessageRevision).where(MessageRevision.chat_id == chat_id))).scalars().all()
    annotations = await db.get(ChatAnnotation, (user_id, chat_id))
    refs = set(body.get('models') or []) | {m.get('model') for m in messages.values() if m.get('model')}
    descriptors = []
    allowed = {item['id']: item for item in await identities(db, SimpleNamespace(id=user_id, role='user'))}
    allowed.update({item['id']: item for item in (directory or [])})
    for reference in sorted(refs):
        public = allowed.get(reference, {'id': reference})
        descriptor = {key: copy.deepcopy(public[key]) for key in ('id', 'name', 'upstream_id') if key in public}
        if isinstance(public.get('connection'), dict):
            descriptor['connection'] = {
                key: public['connection'][key]
                for key in ('id', 'name', 'protocol', 'transport')
                if key in public['connection']
            }
        descriptors.append(descriptor)
    manifest = {
        'schema': SCHEMA,
        'root_chat_id': chat_id,
        'exported_at': now(),
        'chat': body,
        'chat_metadata': {
            key: copy.deepcopy(getattr(chat, key))
            for key in ('created_at', 'updated_at', 'archived', 'pinned', 'variables')
        },
        'billing': {key: list(rows.values()) for key, rows in billing.items()},
        'revisions': [record(row) for row in revisions],
        'annotations': copy.deepcopy(annotations.data) if annotations else {},
        'model_references': descriptors,
    }
    validate(manifest)
    return manifest


def validate(manifest):
    if manifest.get('schema') != SCHEMA or not isinstance(manifest.get('chat'), dict):
        raise ValueError('Unsupported conversation archive')
    root = manifest.get('root_chat_id')
    if not isinstance(root, str) or not root or len(root) > 200:
        raise ValueError('Invalid source conversation')
    messages = manifest['chat'].get('history', {}).get('messages', {})
    if (
        not isinstance(messages, dict)
        or len(messages) > 100000
        or any(not isinstance(message, dict) for message in messages.values())
    ):
        raise ValueError('Invalid message tree')
    visited = set()
    for mid in messages:
        chain, cursor = set(), mid
        while cursor in messages and cursor not in visited:
            if cursor in chain:
                raise ValueError('Cyclic message ancestry')
            chain.add(cursor)
            cursor = messages[cursor].get('parentId')
        visited.update(chain)
    billing = manifest.get('billing') or {}
    maps = {}
    for key in TABLES:
        rows = billing.get(key, [])
        field = 'chat_id' if key == 'ledgers' else 'id'
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ValueError('Invalid archive record count')
        maps[key] = {}
        for row in rows:
            rid = row.get(field) if isinstance(row, dict) else None
            if not isinstance(rid, str) or not rid or len(rid) > 300 or rid in maps[key]:
                raise ValueError('Invalid or duplicated archive record identity')
            maps[key][rid] = row
    for key in ['generations', 'calls', 'snapshots', 'baselines']:
        for row in maps[key].values():
            if row.get('chat_id') not in maps['ledgers']:
                raise ValueError('Missing source ledger')
    if maps['ledgers'] and root not in maps['ledgers']:
        raise ValueError('Missing conversation ledger')
    for row in maps['ledgers'].values():
        if not isinstance(row.get('sequence'), int) or isinstance(row['sequence'], bool) or row['sequence'] < 0:
            raise ValueError('Invalid ledger boundary')
    for row in maps['calls'].values():
        if (
            row.get('generation_id') not in maps['generations']
            or not isinstance(row.get('sequence'), int)
            or row['sequence'] < 1
        ):
            raise ValueError('Invalid call lineage')
        accounting.money(row.get('actual_usd'))
        accounting.money(row.get('estimated_usd'))
        if maps['generations'][row['generation_id']]['chat_id'] != row['chat_id']:
            raise ValueError('Call and generation belong to different ledgers')
        if row['sequence'] > maps['ledgers'][row['chat_id']]['sequence']:
            raise ValueError('Call exceeds its ledger boundary')
    for row in maps['baselines'].values():
        accounting.money(row.get('actual_usd'))
        accounting.money(row.get('estimated_usd'))
    for row in maps['snapshots'].values():
        if row.get('generation_id') and row['generation_id'] not in maps['generations']:
            raise ValueError('Missing snapshot generation')
        if not isinstance(row.get('max_sequence'), int) or row['max_sequence'] < 0:
            raise ValueError('Invalid snapshot boundary')
        if any(ref not in maps['baselines'] for ref in row.get('baseline_ids', [])):
            raise ValueError('Missing snapshot baseline')
        seen, current = set(), row
        while current.get('inherited_snapshot_id'):
            ref = current['inherited_snapshot_id']
            if ref in seen or ref not in maps['snapshots']:
                raise ValueError('Cyclic or missing snapshot inheritance')
            seen.add(ref)
            current = maps['snapshots'][ref]
    for row in maps['ledgers'].values():
        if row.get('inherited_snapshot_id') and row['inherited_snapshot_id'] not in maps['snapshots']:
            raise ValueError('Missing ledger inheritance')
    return maps


async def restore_billing(db, user_id, manifest):
    """Restore one canonical event per original call, including repeated imports."""
    rows = validate(manifest)
    ids = {key: {} for key in TABLES}
    for key, model in TABLES.items():
        for source_id, value in rows[key].items():
            existing = await db.get(model, source_id)
            owner = await db.get(ChatLedger, existing.chat_id) if existing else None
            owned = owner is not None and owner.user_id == user_id
            # A foreign user's identically named source can never be linked or overwritten.
            ids[key][source_id] = source_id if owned else canonical_id(user_id, key, source_id)
    for source_id, value in rows['ledgers'].items():
        target = ids['ledgers'][source_id]
        existing = await db.get(ChatLedger, target)
        if existing and existing.user_id != user_id:
            raise PermissionError('Imported ledger identity unavailable')
        if not existing:
            db.add(
                ChatLedger(
                    chat_id=target,
                    user_id=user_id,
                    sequence=value['sequence'],
                    inherited_snapshot_id=ids['snapshots'].get(value.get('inherited_snapshot_id')),
                )
            )
    await db.flush()
    for key in ['baselines', 'generations', 'calls', 'snapshots']:
        model = TABLES[key]
        for source_id, value in rows[key].items():
            target = ids[key][source_id]
            item = {
                column.key: copy.deepcopy(value[column.key])
                for column in model.__table__.columns
                if column.key in value
            }
            item.update(id=target, chat_id=ids['ledgers'][value['chat_id']])
            if key == 'generations':
                item['user_id'] = user_id
                item['request_id'] = f'archive:{target}'
            if key in {'calls', 'snapshots'}:
                item['generation_id'] = ids['generations'].get(value.get('generation_id'))
            if key == 'calls':
                item['usage'] = {
                    **(item.get('usage') or {}),
                    'archive_origin': {
                        'call_id': (item.get('usage') or {}).get('archive_origin', {}).get('call_id') or source_id
                    },
                }
            if key == 'snapshots':
                item['inherited_snapshot_id'] = ids['snapshots'].get(value.get('inherited_snapshot_id'))
                item['baseline_ids'] = [ids['baselines'][ref] for ref in value.get('baseline_ids', [])]
            existing = await db.get(model, target)
            if existing:
                if existing.chat_id != item['chat_id']:
                    raise ValueError('Archive identity collides with a different ledger')
                for invariant in {
                    'calls': ['sequence', 'generation_id', 'model_id'],
                    'snapshots': ['max_sequence', 'generation_id', 'inherited_snapshot_id'],
                    'baselines': ['batch_id'],
                    'generations': ['message_id'],
                }[key]:
                    if getattr(existing, invariant) != item.get(invariant):
                        raise ValueError('Archive identity conflicts with an existing record')
                # Existing settlement may be newer than this archive. Never overwrite it.
            else:
                db.add(model(**item))
        await db.flush()
    root = manifest['root_chat_id']
    if root not in rows['ledgers']:
        return None, ids
    anchor_id = canonical_id(
        user_id, 'root-anchor', hashlib.sha256(json.dumps(manifest['billing'], sort_keys=True).encode()).hexdigest()
    )
    anchor = await db.get(CostSnapshot, anchor_id)
    if anchor is None:
        ledger = rows['ledgers'][root]
        anchor = CostSnapshot(
            id=anchor_id,
            chat_id=ids['ledgers'][root],
            message_id=manifest['chat'].get('history', {}).get('currentId'),
            generation_id=None,
            max_sequence=ledger['sequence'],
            inherited_snapshot_id=ids['snapshots'].get(ledger.get('inherited_snapshot_id')),
            baseline_ids=[
                ids['baselines'][key] for key, value in rows['baselines'].items() if value['chat_id'] == root
            ],
            created_at=now(),
        )
        db.add(anchor)
        await db.flush()
        anchor.totals = accounting.totals(*(await accounting.snapshot_records(db, anchor.id)))
    return anchor.id, ids


async def restore_records(db, user_id, manifest, chat_id, model_mapping=None):
    validate(manifest)
    digest = hashlib.sha256(
        json.dumps({'manifest': manifest, 'mapping': model_mapping or {}}, sort_keys=True).encode()
    ).hexdigest()
    existing = await db.get(Chat, chat_id)
    if existing:
        if existing.user_id != user_id or (existing.meta or {}).get('oi_archive_digest') != digest:
            raise ValueError('Import identity already belongs to a different conversation')
        return existing
    anchor, ids = await restore_billing(db, user_id, manifest)
    body = copy.deepcopy(manifest['chat'])
    body['id'] = chat_id
    body.pop('share_id', None)
    body.pop('share_mode', None)
    mapping = model_mapping or {}
    body['models'] = [mapping.get(mid, mid) for mid in body.get('models', [])]
    messages = body.setdefault('history', {}).setdefault('messages', {})
    for message in messages.values():
        message['user_id'] = user_id
        message.pop('user', None)
        message.pop('feedbackId', None)
        original = message.get('model')
        if original:
            message['model'] = mapping.get(original, original)
            if message['model'] != original:
                message['oi_native_invalidated'] = True
        if message.get('models'):
            message['models'] = [mapping.get(mid, mid) for mid in message['models']]
        for native in message.get('oi_native', []):
            if isinstance(native, dict) and native.get('call_id') in ids['calls']:
                native['call_id'] = ids['calls'][native['call_id']]
    body['messages'] = []  # The complete message tree is authoritative; no stale linear duplicate.
    stamp = now() // 1000
    chat = Chat(
        id=chat_id,
        user_id=user_id,
        title=body.get('title') or 'Imported conversation',
        chat=body,
        created_at=(manifest.get('chat_metadata') or {}).get('created_at') or stamp,
        updated_at=stamp,
        meta={
            'oi_archive_digest': digest,
            'oi_archive_source': manifest['root_chat_id'],
            'oi_model_descriptors': [
                safe_descriptor(item) for item in manifest.get('model_references', []) if isinstance(item, dict)
            ],
        },
        archived=bool((manifest.get('chat_metadata') or {}).get('archived', False)),
        pinned=bool((manifest.get('chat_metadata') or {}).get('pinned', False)),
        variables=copy.deepcopy((manifest.get('chat_metadata') or {}).get('variables') or {}),
        current_message_id=body['history'].get('currentId'),
    )
    db.add(chat)
    await db.flush()
    # The upstream bulk backfill helper can open/commit its own session. Archive
    # restoration must commit messages, files and billing together or not at all.
    db.add_all(
        [
            ChatMessages._build_message(f'{chat_id}-{mid}', chat_id, user_id, message, stamp)
            for mid, message in messages.items()
        ]
    )
    await db.flush()
    ledger = await accounting.ensure_ledger(db, user_id, chat_id)
    ledger.inherited_snapshot_id = anchor
    for revision in manifest.get('revisions', []):
        db.add(
            MessageRevision(
                id=identifier(),
                chat_id=chat_id,
                message_id=revision['message_id'],
                user_id=user_id,
                content=revision.get('content'),
                output=revision.get('output'),
                created_at=revision['created_at'],
            )
        )
    if manifest.get('annotations'):
        db.add(
            ChatAnnotation(chat_id=chat_id, user_id=user_id, revision=1, data=manifest['annotations'], updated_at=now())
        )
    await db.flush()
    return chat
