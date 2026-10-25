import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.migrations.versions.oi0000000003_message_native_state import upgrade
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import Chat
from open_webui.utils.middleware import process_messages_with_output
from open_webui.utils.oi.protocols import build_request

from test.oi.test_review_regressions import binding, connection, saved_message


@pytest.mark.asyncio
async def test_message_text_edit_preserves_tools_and_records_revision_atomically(db, monkeypatch):
    from contextlib import asynccontextmanager

    from open_webui.models.chats import Chats
    from open_webui.models.customization import MessageRevision
    from sqlalchemy import select

    original = {**saved_message(), 'id': 'm', 'done': True, 'timestamp': 1}
    original['output'].insert(0, {'type': 'function_call', 'name': 'lookup', 'call_id': 'tool-1', 'arguments': '{}'})
    row = Chat(
        id='chat',
        user_id='u',
        title='Test',
        chat={'history': {'messages': {'m': original}, 'currentId': 'm'}},
        created_at=1,
        updated_at=1,
    )
    db.add(row)
    await db.flush()
    await ChatMessages.upsert_message('m', 'chat', 'u', original, db=db)

    @asynccontextmanager
    async def transaction(_id):
        yield db, row
        await db.flush()

    monkeypatch.setattr(Chats, '_chat_transaction', transaction)
    await Chats.edit_reply_text('chat', 'm', 'edited', 'u', {})
    saved = (await ChatMessages.get_messages_map_by_chat_id('chat', db=db))['m']
    assert saved['content'] == 'edited' and saved['oi_native_invalidated']
    assert saved['output'][0] == original['output'][0]
    assert saved['output'][1]['content'][0]['text'] == 'edited'
    revision = (await db.execute(select(MessageRevision))).scalar_one()
    assert revision.content == 'answer' and revision.output == original['output']
    await Chats.edit_reply_text('chat', 'm', 'edited', 'u', {})
    assert len((await db.execute(select(MessageRevision))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_normalized_message_read_retains_replay_and_finish_state(db):
    db.add(Chat(id='chat', user_id='u', title='Test', chat={}, created_at=1, updated_at=1))
    await db.flush()
    message = {**saved_message('rp-preset'), 'id': 'm', 'done': True, 'timestamp': 1, 'finish_reason': 'length'}
    await ChatMessages.upsert_message('m', 'chat', 'u', message, db=db)
    await ChatMessages.upsert_message('m', 'chat', 'u', {'meta': {'task_id': 'next'}}, db=db)
    normalized = (await ChatMessages.get_messages_map_by_chat_id('chat', db=db))['m']
    assert normalized['finish_reason'] == 'length'
    assert normalized['oi_native'] == message['oi_native'] and 'oi_state' not in normalized
    _, payload = build_request({'messages': process_messages_with_output([normalized])}, binding(), connection())
    assert payload['messages'][0]['content'][0]['signature'] == 'synthetic-signature'
    await ChatMessages.upsert_message('m', 'chat', 'u', {'oi_native_invalidated': True, 'content': 'EDITED'}, db=db)
    invalidated = (await ChatMessages.get_messages_map_by_chat_id('chat', db=db))['m']
    assert invalidated['oi_native_invalidated'] is True


def test_migration_backfills_existing_blob_state_without_inventing_history():
    engine = sa.create_engine('sqlite://')
    schema = sa.MetaData()
    chat = sa.Table('chat', schema, sa.Column('id', sa.Text, primary_key=True), sa.Column('chat', sa.JSON))
    messages = sa.Table('chat_message', schema, sa.Column('id', sa.Text, primary_key=True))
    schema.create_all(engine)
    with engine.begin() as db:
        original = saved_message()
        original['finish_reason'] = 'length'
        db.execute(
            chat.insert().values(id='c', chat={'history': {'messages': {'m': original, 'old': {'content': 'legacy'}}}})
        )
        db.execute(messages.insert(), [{'id': 'c-m'}, {'id': 'c-old'}])
        with Operations.context(MigrationContext.configure(db)):
            upgrade()
        reflected = sa.Table('chat_message', sa.MetaData(), autoload_with=db)
        rows = dict(db.execute(sa.select(reflected.c.id, reflected.c.oi_state)).all())
        assert rows['c-m']['oi_native'] == original['oi_native']
        assert rows['c-m']['finish_reason'] == 'length' and rows['c-old'] is None
    engine.dispose()
