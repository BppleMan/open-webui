from decimal import Decimal

import pytest
from open_webui.models.customization import ModelCall
from open_webui.utils.oi.accounting import (
    begin_call,
    begin_generation,
    estimate,
    finish_generation,
    get_totals,
    inherit_snapshot,
    inherit_history_baseline,
    money,
    normalize_usage,
    set_baseline,
    settle_call,
)
from sqlalchemy import select


async def answer(db, chat, message, charge, request=None):
    generation, created = await begin_generation(db, 'u', chat, message, request or f'{chat}-{message}')
    call, _ = await begin_call(db, generation, 'model')
    await settle_call(db, call.id, {'input_tokens': 1, 'output_tokens': 1}, actual_usd=charge)
    snapshot = await finish_generation(db, generation.id)
    return call, snapshot


@pytest.mark.asyncio
async def test_regeneration_fork_boundaries_and_global_uniqueness(db):
    await answer(db, 'parent', 'first', '0.03')
    await answer(db, 'parent', 'regen1', '0.04')
    _, anchor = await answer(db, 'parent', 'regen2', '0.05')
    assert (await get_totals(db, 'parent'))['actual_usd'] == '0.12'
    await answer(db, 'parent', 'later', '0.10')
    await inherit_snapshot(db, 'u', 'fork', 'parent', 'regen2')
    assert (await get_totals(db, 'fork'))['actual_usd'] == '0.12'
    await answer(db, 'fork', 'new', '0.02')
    assert (await get_totals(db, 'fork'))['actual_usd'] == '0.14'
    assert (await get_totals(db, 'parent', anchor.id))['actual_usd'] == '0.12'
    calls = (await db.execute(select(ModelCall))).scalars().all()
    assert len(calls) == 5
    assert sum(Decimal(c.actual_usd) for c in calls) == Decimal('0.24')


@pytest.mark.asyncio
async def test_unknown_estimate_actual_and_late_settlement(db):
    generation, _ = await begin_generation(db, 'u', 'p', 'm', 'r')
    call, _ = await begin_call(db, generation, 'm', pricing={'rates': {'input': '1', 'output': '2'}})
    snapshot = await finish_generation(db, generation.id)
    await inherit_snapshot(db, 'u', 'child', 'p', 'm')
    assert (await get_totals(db, 'p'))['unknown_calls'] == 1
    usage = {'input_tokens': 1000, 'output_tokens': 1000}
    await settle_call(db, call.id, usage)
    assert (await get_totals(db, 'child'))['estimated_usd'] == '0.003'
    await settle_call(db, call.id, usage, actual_usd='0.0015', revision=2)
    await settle_call(db, call.id, usage, revision=1)  # stale replay cannot replace settlement
    assert (await get_totals(db, 'child'))['actual_usd'] == '0.0015'
    assert (await get_totals(db, 'child'))['estimated_usd'] == '0'
    assert (await get_totals(db, 'p', snapshot.id))['unknown_calls'] == 0


@pytest.mark.asyncio
async def test_generation_idempotency_and_baseline_replacement(db):
    first, created = await begin_generation(db, 'u', 'c', 'm', 'request')
    second, replay = await begin_generation(db, 'u', 'c', 'm', 'request')
    assert created and not replay and first.id == second.id
    with pytest.raises(ValueError):
        await begin_generation(db, 'u', 'other', 'm', 'request')
    await set_baseline(db, 'u', 'c', 'batch', actual_usd='12.50')
    await set_baseline(db, 'u', 'c', 'batch', actual_usd='10.00')
    assert (await get_totals(db, 'c'))['actual_usd'] == '10.00'
    await set_baseline(db, 'u', 'c', 'unknown')
    assert (await get_totals(db, 'c'))['unknown_baselines'] == 1


def test_native_token_subsets_and_missing_usage():
    anthropic = normalize_usage(
        {'input_tokens': 10, 'output_tokens': 20, 'cache_read_input_tokens': 80, 'cache_creation_input_tokens': 10},
        'anthropic',
    )
    assert anthropic['input_tokens'] == 100
    assert (
        estimate(anthropic, {'rates': {'input': 1, 'output': 2, 'cache_read': '0.1', 'cache_write': '1.25'}})
        == '0.0000705'
    )
    gemini = normalize_usage({'promptTokenCount': 100, 'candidatesTokenCount': 30, 'thoughtsTokenCount': 20}, 'gemini')
    assert gemini['output_tokens'] == 50
    assert estimate(gemini, {'rates': {'input': 1, 'output': 2}}) == '0.0002'
    assert normalize_usage({}) == {'raw': {}}
    assert estimate(normalize_usage({}), {'rates': {'input': 1, 'output': 1}}) is None
    assert estimate({'input_tokens': 0, 'output_tokens': 0}, {}) is None


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', True])
def test_invalid_money_is_not_a_price(value):
    with pytest.raises(ValueError):
        money(value)


@pytest.mark.asyncio
async def test_fork_uses_latest_call_boundary_when_snapshot_timestamps_tie(db):

    snapshots = []
    for i, cost in enumerate(['0.03', '0.04', '0.05']):
        _, snapshot = await answer(db, 'parent', 'same-message', cost, request=f'attempt-{i}')
        snapshots.append(snapshot)
    for snapshot in snapshots:
        snapshot.created_at = 1
    await db.flush()
    await inherit_snapshot(db, 'u', 'fork', 'parent', 'same-message')
    assert (await get_totals(db, 'fork'))['actual_usd'] == '0.12'


@pytest.mark.asyncio
async def test_imported_history_anchor_inherits_baseline_without_inventing_calls(db):
    from open_webui.models.customization import Generation

    await set_baseline(db, 'u', 'parent', 'imported', actual_usd='12.50')
    await answer(db, 'parent', 'later', '0.04')
    await inherit_history_baseline(db, 'u', 'fork', 'parent', 'historical-message')
    assert (await get_totals(db, 'fork'))['actual_usd'] == '12.50'
    await answer(db, 'fork', 'new', '0.02')
    assert (await get_totals(db, 'fork'))['actual_usd'] == '12.52'
    await set_baseline(db, 'u', 'parent', 'imported', actual_usd='10.00')
    assert (await get_totals(db, 'fork'))['actual_usd'] == '10.02'
    assert len((await db.execute(select(Generation))).scalars().all()) == 2
