import pytest
from open_webui.utils.oi.drafts import DraftConflict, clear_draft, get_draft, list_drafts, put_draft


@pytest.mark.asyncio
async def test_long_draft_revision_conflict_and_send_race(db):
    first = await put_draft(db, 'user', 'chat', {'prompt': '长文' * 10000}, 0, 'first')
    assert first['revision'] == 1
    assert len(first['payload']['prompt']) == 20000
    assert (await put_draft(db, 'user', 'chat', {'prompt': 'ignored'}, 0, 'first')) == first
    second = await put_draft(db, 'user', 'chat', {'prompt': 'new text while sending'}, 1, 'second')
    with pytest.raises(DraftConflict) as error:
        await clear_draft(db, 'user', 'chat', first['revision'], 'sent')
    assert error.value.current == second
    assert (await get_draft(db, 'user', 'chat'))['payload']['prompt'] == 'new text while sending'


@pytest.mark.asyncio
async def test_clear_tombstone_prevents_stale_resurrection(db):
    await put_draft(db, 'u', 'c', {'prompt': 'saved'}, 0, 'one')
    cleared = await clear_draft(db, 'u', 'c', 1, 'clear')
    assert cleared['revision'] == 2
    assert await list_drafts(db, 'u') == []
    with pytest.raises(DraftConflict):
        await put_draft(db, 'u', 'c', {'prompt': 'stale'}, 0, 'old-device')
    assert (await get_draft(db, 'another-user', 'c'))['revision'] == 0


@pytest.mark.asyncio
async def test_acknowledgement_keeps_queue_and_new_input(db):
    from open_webui.utils.oi.drafts import acknowledge_input, acknowledge_queued, get_draft

    queue = [{'id': 'q1', 'requestId': 'r1', 'prompt': 'first'}, {'id': 'q2', 'requestId': 'r2', 'prompt': 'second'}]
    await put_draft(db, 'u', 'c', {'prompt': 'sent input', 'queue': queue}, 0, 'initial')
    acknowledged = await acknowledge_input(db, 'u', 'c', 1, 'input-ack')
    assert acknowledged['payload'] == {'queue': queue}
    await put_draft(db, 'u', 'c', {'prompt': 'keep new input', 'queue': queue}, 2, 'typed')
    acknowledged = await acknowledge_queued(db, 'u', 'c', 'q1', 'r1', 'queue-ack')
    assert acknowledged['payload'] == {'prompt': 'keep new input', 'queue': [queue[1]]}
    replay = await acknowledge_queued(db, 'u', 'c', 'q1', 'r1', 'queue-ack')
    assert replay['revision'] == acknowledged['revision']
    with pytest.raises(DraftConflict):
        await acknowledge_queued(db, 'u', 'c', 'q2', 'stale-request-version', 'wrong-ack')
    assert (await get_draft(db, 'u', 'c'))['payload']['queue'] == [queue[1]]


@pytest.mark.asyncio
async def test_input_ack_ignores_queue_edits_but_not_retyped_text(db):
    from open_webui.utils.oi.drafts import acknowledge_input

    await put_draft(db, 'u', 'c', {'prompt': 'same', '_oi_input_version': 'v1'}, 0, 'm1')
    queue = [{'id': 'q1', 'requestId': 'r1', 'prompt': 'queued'}]
    await put_draft(db, 'u', 'c', {'prompt': 'same', '_oi_input_version': 'v1', 'queue': queue}, 1, 'm2')
    cleared = await acknowledge_input(db, 'u', 'c', 1, 'ack1', 'v1')
    assert cleared['payload'] == {'queue': queue}
    await put_draft(db, 'u', 'c', {'prompt': 'same', '_oi_input_version': 'v2', 'queue': queue}, 3, 'm3')
    replay = await acknowledge_input(db, 'u', 'c', 1, 'ack1', 'v1')
    assert replay['applied'] is False
    assert replay['payload']['prompt'] == 'same'
    assert replay['payload']['_oi_input_version'] == 'v2'
