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
    ArchiveOrigin,
    ChatAnnotation,
    ChatLedger,
    CostSnapshot,
    Generation,
    ImportCostBaseline,
    MessageRevision,
    ModelCall,
)
from open_webui.utils.oi import accounting
from open_webui.utils.oi.storage import identifier, now, lock_user_state
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
    await lock_user_state(db, user_id)
    chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        raise PermissionError('Only the owner can archive a conversation')
    body = copy.deepcopy(chat.chat or {})
    body.setdefault('history', {})['messages'] = await ChatMessages.get_messages_map_by_chat_id(
        chat_id, db=db
    ) or body.get('history', {}).get('messages', {})
    from open_webui.utils.oi.native_privacy import project_native

    await project_native(body, chat.user_id, user_id, db, portable=True)
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
            if generation is None or generation.chat_id != source_chat:
                raise PermissionError('Call does not belong to the owned ledger')
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
        'model_aliases': copy.deepcopy((chat.meta or {}).get('oi_archive_model_mapping') or {}),
    }
    origins = {}
    for key, rows in billing.items():
        origins[key] = {}
        for rid in rows:
            origin = await db.get(ArchiveOrigin, (user_id, key, rid))
            origins[key][rid] = origin.source_id if origin else rid
    manifest['origin_ids'] = origins
    validate(manifest)
    return manifest


def validate(manifest):
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or not isinstance(manifest.get('chat'), dict):
        raise ValueError('Unsupported conversation archive')
    aliases = manifest.get('model_aliases', {})
    if (
        not isinstance(aliases, dict)
        or len(aliases) > 10000
        or any(
            not isinstance(key, str) or not isinstance(value, str) or not key or not value
            for key, value in aliases.items()
        )
    ):
        raise ValueError('Invalid archive model aliases')
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
    visited_snapshots = set()
    for row in maps['snapshots'].values():
        if row.get('generation_id') and row['generation_id'] not in maps['generations']:
            raise ValueError('Missing snapshot generation')
        if not isinstance(row.get('max_sequence'), int) or row['max_sequence'] < 0:
            raise ValueError('Invalid snapshot boundary')
        if any(ref not in maps['baselines'] for ref in row.get('baseline_ids', [])):
            raise ValueError('Missing snapshot baseline')
        seen, current = set(), row
        while current.get('inherited_snapshot_id') and current['id'] not in visited_snapshots:
            ref = current['inherited_snapshot_id']
            if ref in seen or ref not in maps['snapshots']:
                raise ValueError('Cyclic or missing snapshot inheritance')
            seen.add(ref)
            current = maps['snapshots'][ref]
        visited_snapshots.update(seen)
        visited_snapshots.add(row['id'])
    for row in maps['ledgers'].values():
        if row.get('inherited_snapshot_id') and row['inherited_snapshot_id'] not in maps['snapshots']:
            raise ValueError('Missing ledger inheritance')
    validate_relations(manifest, maps)
    return maps


async def restore_billing(db, user_id, manifest):
    """Restore one canonical event per original call, including repeated imports."""
    rows = validate(manifest)
    await lock_user_state(db, user_id)
    ids = {key: {} for key in TABLES}
    for key, model in TABLES.items():
        for source_id, value in rows[key].items():
            origin_id = (manifest.get('origin_ids', {}).get(key, {})).get(source_id, source_id)
            provenance = (
                await db.execute(
                    select(ArchiveOrigin).where(
                        ArchiveOrigin.user_id == user_id,
                        ArchiveOrigin.kind == key,
                        ArchiveOrigin.source_id == origin_id,
                    )
                )
            ).scalar_one_or_none()
            existing = await db.get(model, provenance.local_id if provenance else origin_id)
            owner = await db.get(ChatLedger, existing.chat_id) if existing else None
            owned = owner is not None and owner.user_id == user_id
            # A foreign user's identically named source can never be linked or overwritten.
            target = (
                existing.chat_id
                if key == 'ledgers' and owned
                else existing.id
                if owned
                else canonical_id(user_id, key, origin_id)
            )
            ids[key][source_id] = target
            if not provenance:
                db.add(ArchiveOrigin(user_id=user_id, kind=key, local_id=target, source_id=origin_id))
    for source_id, value in rows['ledgers'].items():
        target = ids['ledgers'][source_id]
        existing = await db.get(ChatLedger, target)
        if existing and existing.user_id != user_id:
            raise PermissionError('Imported ledger identity unavailable')
        if existing:
            inherited = ids['snapshots'].get(value.get('inherited_snapshot_id'))
            if existing.inherited_snapshot_id != inherited:
                raise ValueError('Archive ledger inheritance conflicts with an existing record')
            existing.sequence = max(existing.sequence, value['sequence'])
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
                        'call_id': (item.get('usage') or {}).get('archive_origin', {}).get('call_id')
                        or (manifest.get('origin_ids', {}).get('calls', {})).get(source_id, source_id)
                    },
                }
            if key == 'snapshots':
                item.pop('totals', None)
                item['inherited_snapshot_id'] = ids['snapshots'].get(value.get('inherited_snapshot_id'))
                item['baseline_ids'] = [ids['baselines'][ref] for ref in value.get('baseline_ids', [])]
            existing = await db.get(model, target)
            if existing:
                if existing.chat_id != item['chat_id']:
                    raise ValueError('Archive identity collides with a different ledger')
                for invariant in {
                    'calls': ['sequence', 'generation_id', 'model_id', 'binding_id', 'connection_id', 'pricing'],
                    'snapshots': ['max_sequence', 'generation_id', 'inherited_snapshot_id', 'baseline_ids'],
                    'baselines': ['batch_id'],
                    'generations': ['message_id'],
                }[key]:
                    if getattr(existing, invariant) != item.get(invariant):
                        raise ValueError('Archive identity conflicts with an existing record')
                # Only newer portable settlement revisions may update imported events.
                # A native local bill and user-edited baselines are never overwritten by a file.
                if (
                    key == 'calls'
                    and (existing.usage or {}).get('archive_origin')
                    and (item.get('usage') or {}).get('_revision', 0) > (existing.usage or {}).get('_revision', 0)
                ):
                    await accounting.settle_call(
                        db,
                        target,
                        item['usage'],
                        actual_usd=item.get('actual_usd'),
                        source=item.get('source'),
                        status=item.get('status', 'completed'),
                        revision=item['usage']['_revision'],
                    )
            else:
                db.add(model(**item))
        await db.flush()
    for target in ids['snapshots'].values():
        snapshot = await db.get(CostSnapshot, target)
        snapshot.totals = accounting.totals(*(await accounting.snapshot_records(db, target)))
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
    await lock_user_state(db, user_id)
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
    aliases = {key: mapping.get(value, value) for key, value in manifest.get('model_aliases', {}).items()}
    aliases.update(mapping)
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
            'oi_archive_model_mapping': aliases,
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
    if anchor is None:
        await accounting.set_baseline(
            db,
            user_id,
            chat_id,
            'imported-history',
            note='Unknown cost from legacy archive ' + manifest['root_chat_id'],
        )
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


def validate_relations(manifest, maps):
    """Reject inconsistent references before any database or file mutation."""
    origins = manifest.get('origin_ids', {})
    if not isinstance(origins, dict) or any(key not in TABLES for key in origins):
        raise ValueError('Invalid archive origins')
    for key, refs in origins.items():
        if (
            not isinstance(refs, dict)
            or any(
                rid not in maps[key] or not isinstance(origin, str) or not origin or len(origin) > 300
                for rid, origin in refs.items()
            )
            or len(set(refs.values())) != len(refs)
        ):
            raise ValueError('Invalid or duplicated archive origin')
    messages = manifest['chat'].get('history', {}).get('messages', {})
    current = manifest['chat'].get('history', {}).get('currentId')
    if current is not None and current not in messages:
        raise ValueError('Missing current message')
    for mid, message in messages.items():
        if not isinstance(mid, str) or not mid or len(mid) > 300 or message.get('id', mid) != mid:
            raise ValueError('Invalid message identity')
        if message.get('parentId') is not None and message['parentId'] not in messages:
            raise ValueError('Missing message parent')
        children = message.get('childrenIds', [])
        if not isinstance(children, list) or any(
            child not in messages or messages[child].get('parentId') != mid for child in children
        ):
            raise ValueError('Invalid message children')
    sequences = set()
    for key in ['calls', 'baselines']:
        for row in maps[key].values():
            if row.get('actual_usd') is not None and row.get('estimated_usd') is not None:
                raise ValueError('Actual and estimated cost cannot both be active')
            if key == 'calls':
                pair = (row['chat_id'], row['sequence'])
                if isinstance(row['sequence'], bool) or pair in sequences:
                    raise ValueError('Duplicated or invalid call sequence')
                sequences.add(pair)
                if not isinstance(row.get('usage', {}), dict) or not isinstance(row.get('pricing', {}), dict):
                    raise ValueError('Invalid usage or price snapshot')
                usage = row.get('usage', {})
                for field, value in usage.items():
                    if (field.endswith('_tokens') or field == '_revision') and (
                        not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 2**63 - 1
                    ):
                        raise ValueError('Invalid usage counter')
                for field in ['context', 'raw', 'archive_origin']:
                    if field in usage and not isinstance(usage[field], dict):
                        raise ValueError('Invalid usage metadata')
                if 'archive_origin' in usage and not isinstance(usage['archive_origin'].get('call_id'), str):
                    raise ValueError('Invalid original call identity')
                rates = row.get('pricing', {}).get('rates', {})
                if not isinstance(rates, dict):
                    raise ValueError('Invalid price rates')
                for rate in rates.values():
                    accounting.money(rate)

    for row in maps['snapshots'].values():
        if isinstance(row['max_sequence'], bool) or row['max_sequence'] > maps['ledgers'][row['chat_id']]['sequence']:
            raise ValueError('Snapshot exceeds its ledger boundary')
        if row.get('generation_id') and maps['generations'][row['generation_id']]['chat_id'] != row['chat_id']:
            raise ValueError('Snapshot generation belongs to a different ledger')
        refs = row.get('baseline_ids', [])
        if (
            not isinstance(refs, list)
            or len(refs) != len(set(refs))
            or any(maps['baselines'][ref]['chat_id'] != row['chat_id'] for ref in refs)
        ):
            raise ValueError('Snapshot baseline belongs to a different ledger')
    revisions = manifest.get('revisions', [])
    if not isinstance(revisions, list) or len(revisions) > 100000:
        raise ValueError('Invalid revisions')
    for row in revisions:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get('message_id'), str)
            or not isinstance(row.get('created_at'), int)
        ):
            raise ValueError('Invalid revision')
    if not isinstance(manifest.get('annotations', {}), dict):
        raise ValueError('Invalid annotations')
