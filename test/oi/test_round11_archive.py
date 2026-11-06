"""Actual stream projection -> SQLite rows -> portable archive -> repeated restore."""

import copy
import json
from decimal import Decimal

import pytest
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.customization import ModelCall
from open_webui.utils.oi import accounting, archive
from sqlalchemy import select
from test.oi.round10_helpers import PROTOCOLS
from test.oi.test_round10_combinations import consume, outbound


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('filtered', [False, True])
@pytest.mark.parametrize('destination', ['u', 'other-user'])
async def test_consumer_projection_survives_archival_privacy_and_cost_dedup(
    db, monkeypatch, protocol, filtered, destination
):
    async def transform(event):
        return json.loads(json.dumps(event).replace('original-answer', 'filtered-answer'))

    saved = await consume(
        monkeypatch, protocol, ['<think>reason</think>original-answer'], transform=transform if filtered else None
    )
    saved = await consume(monkeypatch, protocol, [' continued'], initial=saved)
    expected_payload = outbound(saved, protocol)
    saved.update(id='reply', parentId='user-message', childrenIds=[], user_id='u')
    db.add(
        Chat(
            id='source-chat',
            user_id='u',
            title='projection archive',
            created_at=1,
            updated_at=1,
            chat={'models': ['oi:leaf'], 'history': {'messages': {}, 'currentId': 'reply'}},
        )
    )
    await db.flush()
    await ChatMessages.upsert_message(
        'user-message',
        'source-chat',
        'u',
        {
            'id': 'user-message',
            'role': 'user',
            'content': 'Synthetic prompt',
            'parentId': None,
            'childrenIds': ['reply'],
        },
        db=db,
    )
    for index, native in enumerate(saved['oi_native']):
        generation, _ = await accounting.begin_generation(db, 'u', 'source-chat', 'reply', f'request-{index}')
        call, _ = await accounting.begin_call(db, generation, 'oi:leaf')
        await accounting.settle_call(
            db, call.id, {'input_tokens': 10, 'output_tokens': 2}, actual_usd='0.01' if index == 0 else '0.02'
        )
        await accounting.finish_generation(db, generation.id)
        native['call_id'] = call.id
        native['body']['private_request_hint'] = 'SYNTHETIC-PRIVATE-CONNECTION'
    await ChatMessages.upsert_message('reply', 'source-chat', 'u', saved, db=db)
    original_output = copy.deepcopy(saved['output'])
    stored = (await ChatMessages.get_messages_map_by_chat_id('source-chat', db=db))['reply']
    assert stored['output'] == original_output
    assert outbound(stored, protocol) == expected_payload
    manifest = await archive.export_records(db, 'u', 'source-chat')
    assert 'SYNTHETIC-PRIVATE-CONNECTION' not in json.dumps(manifest)
    count_before = len((await db.execute(select(ModelCall))).scalars().all())
    first = await archive.restore_records(db, destination, copy.deepcopy(manifest), 'restored-one')
    count_once = len((await db.execute(select(ModelCall))).scalars().all())
    await archive.restore_records(db, destination, copy.deepcopy(manifest), 'restored-two')
    assert len((await db.execute(select(ModelCall))).scalars().all()) == count_once
    assert count_once == count_before + (0 if destination == 'u' else 2)
    restored = (await ChatMessages.get_messages_map_by_chat_id(first.id, db=db))['reply']
    assert restored['output'] == original_output
    assert all('consumer_output' in native for native in restored['oi_native'])
    actual_payload = outbound(restored, protocol)
    total = await accounting.get_totals(db, first.id)
    assert actual_payload == expected_payload
    assert Decimal(total['actual_usd']) == Decimal('0.03') and total['call_count'] == 2
