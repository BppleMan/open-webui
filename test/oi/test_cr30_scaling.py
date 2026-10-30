import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event

from open_webui.models.customization import ChatLedger, Generation, ModelCall, CostSnapshot
from open_webui.routers import customization


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [200, 800])
async def test_measure_complete_usage_read(db, monkeypatch, size):
    chat = f'benchmark-{size}'
    db.add(ChatLedger(chat_id=chat, user_id='u', sequence=size))
    for index in range(1, size + 1):
        gid = f'g-{index}'
        db.add(
            Generation(
                id=gid,
                user_id='u',
                chat_id=chat,
                message_id=f'm-{index}',
                request_id=f'r-{index}',
                kind='assistant',
                status='completed',
                created_at=index,
                completed_at=index,
            )
        )
        db.add(
            ModelCall(
                id=f'c-{index}',
                generation_id=gid,
                chat_id=chat,
                sequence=index,
                model_id='oi:leaf',
                status='completed',
                usage={},
                actual_usd='0.01',
                pricing={},
                created_at=index,
                completed_at=index,
            )
        )
        db.add(
            CostSnapshot(
                id=f's-{index}',
                chat_id=chat,
                message_id=f'm-{index}',
                generation_id=gid,
                max_sequence=index,
                baseline_ids=[],
                totals={'actual_usd': str(index / 100)},
                created_at=index,
            )
        )
    await db.commit()
    monkeypatch.setattr(customization, 'chat_access', AsyncMock(return_value=SimpleNamespace(user_id='u', meta={})))
    counter = {'model_call_selects': 0, 'returned_call_rows': 0}
    original = db.execute

    async def measured(statement, *args, **kwargs):
        result = await original(statement, *args, **kwargs)
        sql = str(statement)
        if sql.startswith('SELECT ') and 'FROM oi_model_call' in sql:
            counter['model_call_selects'] += 1
            frozen = result.freeze()
            counter['returned_call_rows'] += len(frozen.data)
            return frozen()
        return result

    monkeypatch.setattr(db, 'execute', measured)
    started = time.monotonic()
    result = await customization.chat_usage(chat, SimpleNamespace(id='u'), db)
    record = {
        'size': size,
        **counter,
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'response_bytes': len(json.dumps(result)),
    }

    print(json.dumps(record))
    assert len(result['calls']) == len(result['snapshots']) == size
    # A read should aggregate each call once, not reload each historical prefix.
    assert counter['returned_call_rows'] <= size * 4, record


@pytest.mark.asyncio
async def test_batched_snapshots_match_fixed_boundaries_after_late_settlement(db, monkeypatch):
    from open_webui.utils.oi import accounting
    from test.oi.test_accounting import answer

    await accounting.set_baseline(db, 'u', 'parent', 'unknown-history')
    first, _ = await answer(db, 'parent', 'm1', None)
    await answer(db, 'parent', 'm2', '0.04')
    await accounting.inherit_snapshot(db, 'u', 'child', 'parent', 'm2')
    await answer(db, 'child', 'm3', '0.02')
    await answer(db, 'parent', 'later', '7')

    async def check():
        calls, baselines = await accounting.conversation_records(db, 'child')
        snapshots = await accounting.conversation_snapshots(db, 'child')
        batched = accounting.batch_snapshot_totals(calls, baselines, snapshots)
        for snapshot in snapshots:
            assert batched[snapshot.id] == accounting.totals(*(await accounting.snapshot_records(db, snapshot.id)))
        return batched

    before = await check()
    await accounting.settle_call(db, first.id, {'input_tokens': 12}, actual_usd='0.03', revision=2)
    await accounting.set_baseline(db, 'u', 'parent', 'unknown-history', actual_usd='0.10')
    after = await check()
    assert before != after
    assert (await accounting.get_totals(db, 'child'))['actual_usd'] == '0.19'
    monkeypatch.setattr(customization, 'chat_access', AsyncMock(return_value=SimpleNamespace(user_id='u', meta={})))
    request = SimpleNamespace(headers={})
    response = await customization.chat_usage('child', SimpleNamespace(id='u'), db, request)
    request.headers['if-none-match'] = response.headers['etag']
    unchanged = await customization.chat_usage('child', SimpleNamespace(id='u'), db, request)
    assert unchanged.status_code == 304
    await accounting.settle_call(db, first.id, {}, actual_usd='0.05', revision=3)
    changed = await customization.chat_usage('child', SimpleNamespace(id='u'), db, request)
    assert changed.status_code == 200 and changed.headers['etag'] != response.headers['etag']
