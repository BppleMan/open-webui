"""Per-call accounting and fork snapshots. No chat text is stored in this ledger."""

from decimal import Decimal, InvalidOperation, localcontext

from open_webui.models.customization import ChatLedger, CostSnapshot, Generation, ImportCostBaseline, ModelCall
from open_webui.utils.oi.storage import identifier, insert_once, now, lock_user_state
from sqlalchemy import select, update


def money(value):
    if value is None:
        return None
    if isinstance(value, bool) or len(str(value)) > 128:
        raise ValueError('Invalid monetary value')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Invalid monetary value') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Money must be finite and non-negative')
    if result.adjusted() >= 24 or result.as_tuple().exponent < -36:
        raise ValueError('Monetary value exceeds supported precision or magnitude')
    return format(result, 'f')


def token(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
        return result if result >= 0 and Decimal(str(value)) == result else None
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        return None


def normalize_usage(raw, protocol='openai'):
    """Canonical totals include cache/reasoning subsets; absence is not zero."""
    if not isinstance(raw, dict):
        return {}
    result = {'raw': raw}
    if protocol.startswith('embedding'):
        fields = {
            'input_tokens': raw.get(
                'input_tokens', raw.get('prompt_tokens', raw.get('promptTokenCount', raw.get('prompt_eval_count')))
            ),
            'output_tokens': 0,
        }
        result['output_tokens_not_applicable'] = True
    elif protocol == 'gemini-interactions':
        fields = {
            'input_tokens': raw.get('total_input_tokens'),
            'output_tokens': raw.get('total_output_tokens'),
            'reasoning_tokens': raw.get('total_thought_tokens'),
            'cache_read_tokens': raw.get('total_cached_tokens'),
        }
        if token(fields['output_tokens']) is not None:
            fields['output_tokens'] = token(fields['output_tokens']) + (token(fields['reasoning_tokens']) or 0)
    elif protocol.startswith('gemini') or protocol == 'cpa-agy':
        fields = {
            'input_tokens': raw.get('promptTokenCount'),
            'output_tokens': raw.get('candidatesTokenCount'),
            'reasoning_tokens': raw.get('thoughtsTokenCount'),
            'cache_read_tokens': raw.get('cachedContentTokenCount'),
        }
        if token(fields['output_tokens']) is not None:
            fields['output_tokens'] = token(fields['output_tokens']) + (token(fields['reasoning_tokens']) or 0)
    elif protocol == 'anthropic':
        fields = {
            'input_tokens': raw.get('input_tokens'),
            'output_tokens': raw.get('output_tokens'),
            'cache_read_tokens': raw.get('cache_read_input_tokens'),
            'cache_write_tokens': raw.get('cache_creation_input_tokens'),
            'cache_write_5m_tokens': (raw.get('cache_creation') or {}).get('ephemeral_5m_input_tokens'),
            'cache_write_1h_tokens': (raw.get('cache_creation') or {}).get('ephemeral_1h_input_tokens'),
        }
        if token(fields['input_tokens']) is not None:
            fields['input_tokens'] = (
                token(fields['input_tokens'])
                + (token(fields['cache_read_tokens']) or 0)
                + (token(fields['cache_write_tokens']) or 0)
            )
    else:
        inp = raw.get('input_tokens', raw.get('prompt_tokens', raw.get('prompt_eval_count')))
        out = raw.get('output_tokens', raw.get('completion_tokens', raw.get('eval_count')))
        fields = {
            'input_tokens': inp,
            'output_tokens': out,
            'cache_read_tokens': (raw.get('input_tokens_details') or raw.get('prompt_tokens_details') or {}).get(
                'cached_tokens'
            ),
            'reasoning_tokens': (raw.get('output_tokens_details') or raw.get('completion_tokens_details') or {}).get(
                'reasoning_tokens'
            ),
        }
        input_details = (
            raw.get('input_token_details') or raw.get('input_tokens_details') or raw.get('prompt_tokens_details') or {}
        )
        output_details = (
            raw.get('output_token_details')
            or raw.get('output_tokens_details')
            or raw.get('completion_tokens_details')
            or {}
        )
        cached_details = input_details.get('cached_tokens_details') or {}
        if input_details.get('cached_tokens') is not None:
            fields['cache_read_tokens'] = input_details['cached_tokens']
        for modality in ('audio', 'image'):
            if modality == 'image' and protocol != 'realtime':
                continue
            for prefix, details in [
                ('input', input_details),
                ('output', output_details),
                ('cache_read', cached_details),
            ]:
                if details.get(f'{modality}_tokens') is not None:
                    fields[f'{prefix}_{modality}_tokens'] = details[f'{modality}_tokens']
        if protocol == 'realtime' and (not input_details or not output_details):
            result['unknown_modality_breakdown'] = True
    result.update({key: token(value) for key, value in fields.items() if token(value) is not None})
    if 'input_tokens' in result and 'output_tokens' in result:
        result['total_tokens'] = result['input_tokens'] + result['output_tokens']
    return result


def estimate(usage, pricing):
    with localcontext() as context:
        context.prec = 128
        try:
            return _estimate(usage, pricing)
        except ValueError:
            return None


def _estimate(usage, pricing):
    """Rates are USD per million tokens, captured when the call starts."""
    if 'input_tokens' not in usage or 'output_tokens' not in usage or usage.get('unknown_modality_breakdown'):
        return None
    if pricing.get('currency', 'USD') != 'USD':
        return None
    rates = dict(pricing.get('rates') or {})
    for tier in sorted(pricing.get('tiers') or [], key=lambda t: t['min_input_tokens']):
        if usage['input_tokens'] >= tier['min_input_tokens']:
            rates.update(tier.get('rates') or {})
    read = usage.get('cache_read_tokens', 0)
    write = usage.get('cache_write_tokens', 0)
    reasoning = usage.get('reasoning_tokens', 0) if 'reasoning' in rates else 0
    counts = {
        'input': usage['input_tokens'] - read - write,
        'output': usage['output_tokens'] - reasoning,
        'cache_read': read,
        'cache_write': write,
        'reasoning': reasoning,
    }
    for modality in ('audio', 'image'):
        inp, out, cached = (usage.get(f'{kind}_{modality}_tokens', 0) for kind in ('input', 'output', 'cache_read'))
        if inp and read and f'cache_read_{modality}_tokens' not in usage:
            return None  # A shared cache total cannot identify the modality's unit price.
        if inp < cached:
            return None
        counts['input'] -= inp - cached
        counts['output'] -= out
        counts['cache_read'] -= cached
        counts.update({f'input_{modality}': inp - cached, f'output_{modality}': out, f'cache_read_{modality}': cached})
    if 'cache_write_5m_tokens' in usage or 'cache_write_1h_tokens' in usage:
        short, long = usage.get('cache_write_5m_tokens', 0), usage.get('cache_write_1h_tokens', 0)
        if short + long != write:
            return None
        if 'cache_write_5m' in rates or 'cache_write_1h' in rates:
            counts.pop('cache_write')
            counts.update(cache_write_5m=short, cache_write_1h=long)
    value = Decimal(0)
    for kind, count in counts.items():
        if count < 0 or (count and rates.get(kind) is None):
            return None
        if count:
            value += Decimal(money(rates[kind])) * count / Decimal(1_000_000)
    # A missing rate card must not imply a free call even if zero counters were reported.
    return money(value) if rates else None


async def ensure_ledger(db, user_id, chat_id):
    await lock_user_state(db, user_id)
    await insert_once(
        db,
        ChatLedger,
        {
            'chat_id': chat_id,
            'user_id': user_id,
            'sequence': 0,
            'inherited_snapshot_id': None,
        },
        ['chat_id'],
    )
    ledger = await db.get(ChatLedger, chat_id, populate_existing=True)
    if ledger.user_id != user_id:
        raise PermissionError('Ledger owner mismatch')
    return ledger


async def begin_generation(db, user_id, chat_id, message_id, request_id, kind='assistant'):
    await ensure_ledger(db, user_id, chat_id)
    values = dict(
        id=identifier(),
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        request_id=request_id,
        kind=kind,
        status='pending',
        created_at=now(),
    )
    created = await insert_once(db, Generation, values, ['user_id', 'request_id'])
    generation = (
        await db.execute(
            select(Generation).where(
                Generation.user_id == user_id,
                Generation.request_id == request_id,
            )
        )
    ).scalar_one()
    if generation.chat_id != chat_id or generation.message_id != message_id:
        raise ValueError('Idempotency key was already used for a different generation')
    return generation, created


async def begin_call(db, generation, model_id, *, binding_id=None, connection_id=None, pricing=None, call_id=None):
    await lock_user_state(db, generation.user_id)
    call_id = call_id or identifier()
    existing = await db.get(ModelCall, call_id)
    if existing:
        if existing.generation_id != generation.id:
            raise ValueError('Call identity mismatch')
        return existing, False
    sequence = (
        await db.execute(
            update(ChatLedger)
            .where(ChatLedger.chat_id == generation.chat_id)
            .values(sequence=ChatLedger.sequence + 1)
            .returning(ChatLedger.sequence)
        )
    ).scalar_one()
    created = await insert_once(
        db,
        ModelCall,
        dict(
            id=call_id,
            generation_id=generation.id,
            chat_id=generation.chat_id,
            sequence=sequence,
            binding_id=binding_id,
            connection_id=connection_id,
            model_id=model_id,
            status='pending',
            usage={},
            actual_usd=None,
            estimated_usd=None,
            pricing=pricing or {},
            source=None,
            created_at=now(),
        ),
        ['id'],
    )
    return await db.get(ModelCall, call_id, populate_existing=True), created


async def settle_call(db, call_id, usage, *, actual_usd=None, source=None, status='completed', revision=1):
    call = await db.get(ModelCall, call_id)
    if not call:
        raise ValueError('Unknown call')
    owner = await db.get(ChatLedger, call.chat_id)
    await lock_user_state(db, owner.user_id)
    call = (
        await db.execute(
            select(ModelCall).where(ModelCall.id == call_id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one()
    if revision < (call.usage or {}).get('_revision', 0):
        return call
    call.usage = merge_usage(call.usage or {}, usage)
    call.usage['_revision'] = revision
    # Confirmed actual cost supersedes the estimate for this call; the two columns are disjoint.
    if actual_usd is not None:
        call.actual_usd = money(actual_usd)
    call.estimated_usd = None if call.actual_usd is not None else estimate(call.usage, call.pricing)
    call.source = source or call.source
    call.status = status
    call.completed_at = now()
    await db.flush()
    # The fixed boundary selects direct snapshots; descendants inherit those exact events.
    direct = (
        (
            await db.execute(
                select(CostSnapshot).where(
                    CostSnapshot.chat_id == call.chat_id, CostSnapshot.max_sequence >= call.sequence
                )
            )
        )
        .scalars()
        .all()
    )
    await refresh_snapshots(db, direct)
    return call


async def finish_generation(db, generation_id, status='completed'):
    generation = await db.get(Generation, generation_id)
    if not generation:
        raise ValueError('Unknown generation')
    await lock_user_state(db, generation.user_id)
    generation.status = status
    generation.completed_at = now()
    ledger = await db.get(ChatLedger, generation.chat_id, populate_existing=True)
    baseline_ids = list(
        (
            await db.execute(
                select(ImportCostBaseline.id).where(
                    ImportCostBaseline.chat_id == generation.chat_id,
                )
            )
        ).scalars()
    )
    await insert_once(
        db,
        CostSnapshot,
        dict(
            id=identifier(),
            chat_id=generation.chat_id,
            message_id=generation.message_id,
            generation_id=generation.id,
            max_sequence=ledger.sequence,
            inherited_snapshot_id=ledger.inherited_snapshot_id,
            baseline_ids=baseline_ids,
            created_at=now(),
        ),
        ['generation_id'],
    )
    snapshot = (await db.execute(select(CostSnapshot).where(CostSnapshot.generation_id == generation_id))).scalar_one()
    snapshot.totals = totals(*(await snapshot_records(db, snapshot.id)))
    await db.flush()
    return snapshot


async def snapshot_records(db, snapshot_id):
    calls, baselines, seen = {}, {}, set()
    while snapshot_id:
        if snapshot_id in seen:
            raise ValueError('Cyclic cost inheritance')
        seen.add(snapshot_id)
        snapshot = await db.get(CostSnapshot, snapshot_id)
        if snapshot is None:
            raise ValueError('Missing inherited cost snapshot')
        for call in (
            await db.execute(
                select(ModelCall).where(
                    ModelCall.chat_id == snapshot.chat_id,
                    ModelCall.sequence <= snapshot.max_sequence,
                )
            )
        ).scalars():
            calls[call.id] = call
        if snapshot.baseline_ids:
            for baseline in (
                await db.execute(
                    select(ImportCostBaseline).where(
                        ImportCostBaseline.id.in_(snapshot.baseline_ids),
                    )
                )
            ).scalars():
                baselines[baseline.id] = baseline
        snapshot_id = snapshot.inherited_snapshot_id
    return calls, baselines


def totals(calls, baselines):
    with localcontext() as context:
        context.prec = 128
        return _totals(calls, baselines)


def _totals(calls, baselines):
    actual, estimated, unknown = Decimal(0), Decimal(0), 0
    for item in [*calls.values(), *baselines.values()]:
        if item.actual_usd is not None:
            actual += Decimal(item.actual_usd)
        elif item.estimated_usd is not None:
            estimated += Decimal(item.estimated_usd)
        else:
            unknown += 1
    return {
        'actual_usd': format(actual, 'f'),
        'estimated_usd': format(estimated, 'f'),
        'unknown_calls': sum(c.actual_usd is None and c.estimated_usd is None for c in calls.values()),
        'unknown_baselines': sum(b.actual_usd is None and b.estimated_usd is None for b in baselines.values()),
        'incomplete': unknown > 0,
        'call_count': len(calls),
    }


async def get_totals(db, chat_id, snapshot_id=None):
    if snapshot_id:
        snapshot = await db.get(CostSnapshot, snapshot_id)
        if snapshot is None or snapshot.chat_id != chat_id:
            raise ValueError('Snapshot does not belong to this conversation')
        return totals(*(await snapshot_records(db, snapshot_id)))
    return totals(*(await conversation_records(db, chat_id)))


async def conversation_records(db, chat_id):
    ledger = await db.get(ChatLedger, chat_id, populate_existing=True)
    calls, baselines = await snapshot_records(db, ledger.inherited_snapshot_id) if ledger else ({}, {})
    for call in (await db.execute(select(ModelCall).where(ModelCall.chat_id == chat_id))).scalars():
        calls[call.id] = call
    for baseline in (
        await db.execute(select(ImportCostBaseline).where(ImportCostBaseline.chat_id == chat_id))
    ).scalars():
        baselines[baseline.id] = baseline
    return calls, baselines


async def conversation_snapshots(db, chat_id):
    snapshots = {}
    pending = [(chat_id, None, None, None)]
    covered = {}
    while pending:
        source, boundary, cutoff, baseline_ids = pending.pop()
        ledger = await db.get(ChatLedger, source)
        if not ledger:
            continue
        boundary = ledger.sequence if boundary is None else boundary
        if source in covered and covered[source] >= boundary:
            continue
        covered[source] = boundary
        for snapshot in (
            await db.execute(
                select(CostSnapshot).where(CostSnapshot.chat_id == source, CostSnapshot.max_sequence <= boundary)
            )
        ).scalars():
            if cutoff is not None and (
                snapshot.created_at > cutoff or not set(snapshot.baseline_ids).issubset(baseline_ids)
            ):
                continue
            snapshots[snapshot.id] = snapshot
        if ledger.inherited_snapshot_id:
            anchor = await db.get(CostSnapshot, ledger.inherited_snapshot_id)
            if anchor:
                pending.append((anchor.chat_id, anchor.max_sequence, anchor.created_at, set(anchor.baseline_ids)))
    return sorted(snapshots.values(), key=lambda row: (row.created_at, row.max_sequence, row.id))


async def inherit_snapshot(db, user_id, new_chat_id, parent_chat_id, message_id):
    parent = await db.get(ChatLedger, parent_chat_id)
    if not parent or parent.user_id != user_id:
        return None  # Shared chat forks must not expose the original owner's private bill.
    candidates = [s for s in await conversation_snapshots(db, parent_chat_id) if s.message_id == message_id]
    snapshot = max(candidates, key=lambda s: (s.created_at, s.max_sequence, s.id), default=None)
    if snapshot is None:
        return None
    ledger = await ensure_ledger(db, user_id, new_chat_id)
    if ledger.sequence or ledger.inherited_snapshot_id not in {None, snapshot.id}:
        raise ValueError('Cannot replace an active conversation cost lineage')
    ledger.inherited_snapshot_id = snapshot.id
    await db.flush()
    return snapshot.id


async def inherit_history_baseline(db, user_id, new_chat_id, parent_chat_id, message_id):
    """Anchor history without a generation, excluding all later calls in its parent."""
    parent = await db.get(ChatLedger, parent_chat_id)
    if not parent or parent.user_id != user_id:
        return None
    ledger = await ensure_ledger(db, user_id, new_chat_id)
    if ledger.sequence:
        raise ValueError('Cannot replace an active conversation cost lineage')
    if ledger.inherited_snapshot_id:
        return ledger.inherited_snapshot_id
    baseline_ids = list(
        (await db.execute(select(ImportCostBaseline.id).where(ImportCostBaseline.chat_id == parent_chat_id))).scalars()
    )
    if not baseline_ids and not parent.inherited_snapshot_id:
        return None
    snapshot = CostSnapshot(
        id=identifier(),
        chat_id=parent_chat_id,
        message_id=message_id,
        generation_id=None,
        max_sequence=0,
        inherited_snapshot_id=parent.inherited_snapshot_id,
        baseline_ids=baseline_ids,
        created_at=now(),
    )
    db.add(snapshot)
    await db.flush()
    snapshot.totals = totals(*(await snapshot_records(db, snapshot.id)))
    ledger.inherited_snapshot_id = snapshot.id
    await db.flush()
    return snapshot.id


async def set_baseline(db, user_id, chat_id, batch_id, actual_usd=None, estimated_usd=None, note='', revision=None):
    if actual_usd is not None and estimated_usd is not None:
        raise ValueError('Choose actual or estimated cost for one import batch')
    await ensure_ledger(db, user_id, chat_id)
    actual_usd, estimated_usd = money(actual_usd), money(estimated_usd)
    stamp = now()
    created = await insert_once(
        db,
        ImportCostBaseline,
        dict(
            id=identifier(),
            chat_id=chat_id,
            batch_id=batch_id,
            actual_usd=actual_usd,
            estimated_usd=estimated_usd,
            note=note,
            revision=1,
            created_at=stamp,
            updated_at=stamp,
        ),
        ['chat_id', 'batch_id'],
    )
    baseline = (
        await db.execute(
            select(ImportCostBaseline)
            .where(
                ImportCostBaseline.chat_id == chat_id,
                ImportCostBaseline.batch_id == batch_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if revision is not None and revision != (0 if created else baseline.revision):
        raise BaselineConflict('Cost baseline changed; reload before saving')
    baseline.actual_usd, baseline.estimated_usd, baseline.note = actual_usd, estimated_usd, note
    if not created:
        baseline.revision += 1
    baseline.updated_at = stamp
    await db.flush()
    direct = (await db.execute(select(CostSnapshot).where(CostSnapshot.chat_id == chat_id))).scalars().all()
    await refresh_snapshots(db, [snapshot for snapshot in direct if baseline.id in snapshot.baseline_ids])
    return baseline


class BaselineConflict(ValueError):
    pass


def merge_usage(previous, incoming):
    """Partial cumulative snapshots replace known fields, never add or erase absent counters."""
    result = {**previous, **{key: value for key, value in incoming.items() if value is not None}}
    if isinstance(previous.get('raw'), dict) and isinstance(incoming.get('raw'), dict):
        result['raw'] = {**previous['raw'], **incoming['raw']}
    if 'input_tokens' in result and 'output_tokens' in result:
        result['total_tokens'] = result['input_tokens'] + result['output_tokens']
    return result


async def refresh_snapshots(db, direct):
    affected = {snapshot.id: snapshot for snapshot in direct}
    frontier = set(affected)
    while frontier:
        descendants = (
            (await db.execute(select(CostSnapshot).where(CostSnapshot.inherited_snapshot_id.in_(frontier))))
            .scalars()
            .all()
        )
        frontier = {snapshot.id for snapshot in descendants if snapshot.id not in affected}
        affected.update({snapshot.id: snapshot for snapshot in descendants})
    for snapshot in affected.values():
        snapshot.totals = totals(*(await snapshot_records(db, snapshot.id)))
    await db.flush()


def batch_snapshot_totals(calls, baselines, snapshots):
    """One linear prefix index per source; never reload a call for each round."""
    from bisect import bisect_right
    from collections import defaultdict

    grouped = defaultdict(list)
    for call in calls.values():
        grouped[call.chat_id].append(call)
    prefixes = {}
    with localcontext() as context:
        context.prec = 128
        for chat_id, rows in grouped.items():
            sequences, sums = [], [(Decimal(0), Decimal(0), 0, 0)]
            for call in sorted(rows, key=lambda c: (c.sequence, c.id)):
                a, e, u, n = sums[-1]
                if call.actual_usd is not None:
                    a += Decimal(call.actual_usd)
                elif call.estimated_usd is not None:
                    e += Decimal(call.estimated_usd)
                else:
                    u += 1
                sequences.append(call.sequence)
                sums.append((a, e, u, n + 1))
            prefixes[chat_id] = sequences, sums
        lookup = {s.id: s for s in snapshots}
        result = {}
        for row in snapshots:
            bounds, baseline_ids, visited = {}, set(), set()
            current = row
            while current:
                if current.id in visited:
                    raise ValueError('Cyclic cost inheritance')
                visited.add(current.id)
                bounds[current.chat_id] = max(bounds.get(current.chat_id, 0), current.max_sequence)
                baseline_ids.update(current.baseline_ids)
                if current.inherited_snapshot_id and current.inherited_snapshot_id not in lookup:
                    raise ValueError('Missing inherited cost snapshot')
                current = lookup.get(current.inherited_snapshot_id)
            a, e, u, n = Decimal(0), Decimal(0), 0, 0
            for chat_id, boundary in bounds.items():
                sequences, sums = prefixes.get(chat_id, ([], [(Decimal(0), Decimal(0), 0, 0)]))
                ca, ce, cu, cn = sums[bisect_right(sequences, boundary)]
                a += ca
                e += ce
                u += cu
                n += cn
            base = totals({}, {key: baselines[key] for key in baseline_ids if key in baselines})
            result[row.id] = {
                'actual_usd': format(a + Decimal(base['actual_usd']), 'f'),
                'estimated_usd': format(e + Decimal(base['estimated_usd']), 'f'),
                'unknown_calls': u,
                'unknown_baselines': base['unknown_baselines'],
                'incomplete': bool(u or base['unknown_baselines']),
                'call_count': n,
            }
        return result
