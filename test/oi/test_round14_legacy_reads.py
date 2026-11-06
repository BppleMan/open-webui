"""Normalized current messages must not load the full legacy transcript unnecessarily."""

import json
import pytest
from sqlalchemy import event
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessages


@pytest.mark.asyncio
@pytest.mark.parametrize('user_message', [False, True])
@pytest.mark.parametrize('terminal', ['current', 'null', 'legacy', 'new'])
@pytest.mark.parametrize('size', [32, 1024 * 1024])
async def test_current_rows_skip_legacy_transcript_fetch(db, user_message, terminal, size):
    answer = {
        'id': 'reply',
        'role': 'assistant',
        'content': 'answer',
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'answer'}]}],
        'response_status': 'completed',
        'incomplete_details': None,
    }
    messages = {'reply': answer}
    if user_message:
        messages['question'] = {'id': 'question', 'role': 'user', 'content': 'x' * size, 'childrenIds': ['reply']}
    db.add(
        Chat(
            id='reading',
            user_id='u',
            title='synthetic',
            created_at=1,
            updated_at=1,
            chat={'history': {'currentId': 'reply', 'messages': messages}},
        )
    )
    await db.flush()
    for mid, message in messages.items():
        data = dict(message)
        if mid == 'reply':
            if terminal == 'new':
                data.pop('response_status')
                data.pop('incomplete_details')
                data.update(content='', output=[], done=False)
            elif terminal == 'legacy':
                data.pop('response_status')
                data.pop('incomplete_details')
            elif terminal == 'null':
                data.update(response_status=None, incomplete_details=None)
        await ChatMessages.upsert_message(mid, 'reading', 'u', data, db=db)
    sql = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        sql.append(statement)

    engine = db.sync_session.bind
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        for _ in range(3):
            result = await ChatMessages.get_messages_map_by_chat_id('reading', db=db)
            assert result['reply']['content'] == ('' if terminal == 'new' else 'answer')
            if terminal == 'legacy':
                assert result['reply'].get('response_status') == 'completed'
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    legacy = [s for s in sql if 'SELECT chat.chat' in s]
    if terminal == 'legacy':
        assert legacy
    else:
        assert not legacy, {'unnecessary_full_legacy_loads': len(legacy), 'transcript_bytes': len(json.dumps(messages))}
