"""Authenticated copy and side-chat state transfer, with no temporary transcript writes."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from open_webui.models.chats import Chat, Chats
from open_webui.models.chat_messages import ChatMessages
from open_webui.utils.oi.native_transfer import transfer_capsule, side_context
from open_webui.utils.oi.temporary_native import seal, restore


def message():
    return {
        'id': 'source',
        'role': 'assistant',
        'model': 'oi:leaf',
        'content': 'original',
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'original'}]}],
    }


def native():
    return [
        {
            'call_id': 'call',
            'binding_id': 'oi:leaf',
            'protocol': 'gemini',
            'replayable': True,
            'body': {'candidates': [{'content': {'parts': [{'text': 'original', 'thoughtSignature': 'signed'}]}}]},
        }
    ]


@pytest.mark.parametrize('edited', [False, True])
@pytest.mark.parametrize('different_conversation', [False, True])
def test_copy_rebinds_only_unchanged_authenticated_output(edited, different_conversation):
    source = message()
    source['oi_native_state'] = seal(native(), source, 'owner', 'parent', 'source')
    before = deepcopy(source)
    destination = {**deepcopy(source), 'id': 'copy'}
    if edited:
        destination['output'][0]['content'][0]['text'] = 'edited'
    target = 'side' if different_conversation else 'parent'
    destination.update(transfer_capsule(source, destination, 'owner', 'parent', target))
    calls = restore(destination, 'owner', target)
    assert bool(calls) is not edited
    assert bool(destination.get('oi_native_invalidated')) is edited
    assert source == before
    if not edited:
        assert calls == native()


@pytest.mark.parametrize('changed', ['user', 'conversation', 'message', 'ciphertext'])
def test_copy_rejects_wrong_source_identity(changed):
    source = message()
    source['oi_native_state'] = seal(native(), source, 'owner', 'parent', 'source')
    user, conversation = 'owner', 'parent'
    if changed == 'user':
        user = 'other'
    if changed == 'conversation':
        conversation = 'other'
    if changed == 'message':
        source['id'] = 'other'
    if changed == 'ciphertext':
        source['oi_native_state'] = (
            source['oi_native_state'][:40]
            + ('A' if source['oi_native_state'][40] != 'A' else 'B')
            + source['oi_native_state'][41:]
        )
    with pytest.raises(HTTPException) as error:
        transfer_capsule(source, {**source, 'id': 'copy'}, user, conversation, 'side')
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('edited', [False, True])
async def test_saved_parent_context_is_authorized_and_not_mutated(db, monkeypatch, edited):
    source = message()
    source['oi_native'] = native()
    row = Chat(
        id='parent',
        user_id='owner',
        title='synthetic',
        created_at=1,
        updated_at=1,
        chat={'history': {'currentId': 'source', 'messages': {'source': source}}},
    )
    db.add(row)
    await db.flush()
    await ChatMessages.upsert_message('source', 'parent', 'owner', source, db=db)
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=row))
    requested = deepcopy(source)
    requested.pop('oi_native')
    if edited:
        requested['output'][0]['content'][0]['text'] = 'edited'
    before = deepcopy(row.chat)
    patches = await side_context(
        db, SimpleNamespace(id='owner', role='user'), 'side', [requested], source_chat_id='parent'
    )
    requested.update(patches[0])
    calls = restore(requested, 'owner', 'side')
    assert bool(calls) is not edited and row.chat == before
    assert not (await db.get(Chat, 'side'))
    assert 'signed' not in str(patches)


@pytest.mark.asyncio
async def test_inaccessible_parent_has_no_native_transfer(db, monkeypatch):
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await side_context(db, SimpleNamespace(id='other', role='user'), 'side', [message()], source_chat_id='private')
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_temporary_parent_requires_original_capsule_binding(db):
    source = message()
    source['oi_native_state'] = seal(native(), source, 'owner', 'parent', 'source')
    patches = await side_context(db, SimpleNamespace(id='owner'), 'side', [source], source_temporary_id='parent')
    copied = {**source, **patches[0]}
    assert restore(copied, 'owner', 'side') == native()
    with pytest.raises(HTTPException):
        restore({**source, **patches[0]}, 'owner', 'parent')
    with pytest.raises(HTTPException):
        await side_context(db, SimpleNamespace(id='other'), 'side', [source], source_temporary_id='parent')


@pytest.mark.asyncio
async def test_new_temporary_session_registry_works_without_redis():
    from uuid import uuid4
    from open_webui.utils.chat import register_temporary_chat_session, get_temporary_chat, _temporary_chat_owners

    chat_id = 'temporary:socket-one:' + str(uuid4())
    try:
        assert await register_temporary_chat_session(None, chat_id, 'owner', 'socket-one', 'owner', create=True)
        assert not await register_temporary_chat_session(None, chat_id, 'other', 'socket-two', 'other')
        assert await register_temporary_chat_session(None, chat_id, 'owner', 'socket-two', 'owner')
        assert (await get_temporary_chat(None, chat_id))['session_id'] == 'socket-two'
    finally:
        _temporary_chat_owners.pop(chat_id, None)
