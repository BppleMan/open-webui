import copy
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.models.customization import Generation, ModelCall
from open_webui.utils.oi import runtime
from sqlalchemy import select


@pytest.mark.asyncio
async def test_batch_reservation_rejects_retry_without_partial_records(db, monkeypatch):
    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    user = SimpleNamespace(id='u')
    metadata = {'chat_id': 'chat', 'oi_request_id': 'request'}
    entries = [{'model_id': 'same', 'message_id': 'left'}, {'model_id': 'same', 'message_id': 'right'}]
    result = await runtime.reserve_generations(user, metadata, copy.deepcopy(entries))
    assert set(result) == {'left', 'right'} and len(set(result.values())) == 2
    with pytest.raises(HTTPException) as retry:
        await runtime.reserve_generations(user, metadata, copy.deepcopy(entries))
    assert retry.value.status_code == 409 and retry.value.detail['generation_id'] == result['left']
    with pytest.raises(HTTPException):
        await runtime.reserve_generations(user, metadata, [{'model_id': 'same', 'message_id': 'new'}, entries[0]])
    rows = (await db.execute(select(Generation))).scalars().all()
    assert {g.message_id for g in rows} == {'left', 'right'}
    assert (await db.execute(select(ModelCall))).scalars().all() == []


@pytest.mark.asyncio
async def test_api_retry_without_message_id_uses_stable_slot_identity(db, monkeypatch):
    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    user = SimpleNamespace(id='u')
    first = [{'model_id': 'm'}]
    result = await runtime.reserve_generations(user, {'chat_id': 'new-chat-1', 'oi_request_id': 'request'}, first)
    retry_entries = [{'model_id': 'm'}]
    with pytest.raises(HTTPException) as retry:
        await runtime.reserve_generations(user, {'chat_id': 'new-chat-2', 'oi_request_id': 'request'}, retry_entries)
    assert retry.value.detail['chat_id'] == 'new-chat-1'
    assert first == retry_entries
    assert retry.value.detail['generation_id'] == result[first[0]['message_id']]
    # Explicit new attempts and other users have independent reservations.
    second = await runtime.reserve_generations(
        user, {'chat_id': 'new-chat-1', 'oi_request_id': 'next'}, [{'model_id': 'm'}]
    )
    assert set(result.values()).isdisjoint(second.values())
