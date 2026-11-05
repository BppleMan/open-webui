"""Cross-feature invariants: fork boundaries, portable billing and late settlement."""

import copy
from decimal import Decimal

import pytest
from open_webui.models.chats import Chat
from open_webui.models.customization import ModelCall
from open_webui.utils.oi import accounting, archive
from sqlalchemy import select


async def answer(db, chat, message, amount):
    generation, _ = await accounting.begin_generation(db, 'u', chat, message, chat + '-' + message)
    call, _ = await accounting.begin_call(db, generation, 'synthetic-model')
    await accounting.settle_call(db, call.id, {'input_tokens': 10, 'output_tokens': 2}, actual_usd=amount)
    await accounting.finish_generation(db, generation.id)
    return call


@pytest.mark.asyncio
@pytest.mark.parametrize('destination', ['u', 'other-user'])
@pytest.mark.parametrize('nested', [False, True])
async def test_fork_archive_repeat_import_and_late_settlement(db, destination, nested):
    def add_chat(cid, messages):
        tree = {
            key: {'id': key, 'role': 'assistant', 'content': key, 'parentId': None, 'childrenIds': []}
            for key in messages
        }
        db.add(
            Chat(
                id=cid,
                user_id='u',
                title='synthetic',
                created_at=1,
                updated_at=1,
                chat={'history': {'messages': tree, 'currentId': messages[-1]}},
            )
        )

    add_chat('parent', ['m1', 'later'])
    add_chat('child', ['m1', 'm2'])
    await db.flush()
    original = await answer(db, 'parent', 'm1', '0.03')
    await answer(db, 'parent', 'later', '0.80')
    await accounting.inherit_snapshot(db, 'u', 'child', 'parent', 'm1')
    await answer(db, 'child', 'm2', '0.02')
    source = 'child'
    if nested:
        add_chat('grandchild', ['m1', 'm2', 'm3'])
        await db.flush()
        await accounting.inherit_snapshot(db, 'u', 'grandchild', 'child', 'm2')
        await answer(db, 'grandchild', 'm3', '0.01')
        source = 'grandchild'
    expected = Decimal('0.06' if nested else '0.05')
    manifest = await archive.export_records(db, 'u', source)
    first = await archive.restore_records(db, destination, copy.deepcopy(manifest), 'copy1')
    calls_before = len((await db.execute(select(ModelCall))).scalars().all())
    second = await archive.restore_records(db, destination, copy.deepcopy(manifest), 'copy2')
    assert len((await db.execute(select(ModelCall))).scalars().all()) == calls_before
    for cid in [source, first.id, second.id]:
        assert Decimal((await accounting.get_totals(db, cid))['actual_usd']) == expected
    await accounting.settle_call(
        db, original.id, {'input_tokens': 10, 'output_tokens': 2}, actual_usd='0.035', revision=2
    )
    if destination != 'u':
        assert Decimal((await accounting.get_totals(db, first.id))['actual_usd']) == expected
    fresh = await archive.export_records(db, 'u', source)
    await archive.restore_records(db, destination, copy.deepcopy(fresh), 'copy3')
    for cid in [source, first.id, second.id, 'copy3']:
        total = await accounting.get_totals(db, cid)
        assert Decimal(total['actual_usd']) == expected + Decimal('0.005'), (cid, total)
        assert total['call_count'] == (3 if nested else 2)
