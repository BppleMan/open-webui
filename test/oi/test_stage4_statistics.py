from types import SimpleNamespace
import pytest
from sqlalchemy import select, delete
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.customization import ChatLedger, Generation, ModelCall, Connection, ModelBinding
from open_webui.utils.oi import statistics, history


@pytest.mark.asyncio
async def test_statistics_use_original_calls_not_fork_message_projections(db):
    stamp = 1760000000
    for key in ['source', 'fork', 'archive', 'legacy']:
        db.add(Chat(id=key, user_id='u', title=key, chat={}, created_at=stamp, updated_at=stamp))
        if key != 'legacy':
            db.add(
                ChatLedger(
                    chat_id=key,
                    user_id='u',
                    sequence=1,
                    inherited_snapshot_id='source-anchor' if key in {'fork', 'archive'} else None,
                )
            )
        db.add(
            ChatMessage(
                id=key + '-message',
                chat_id=key,
                user_id='u',
                role='assistant',
                model_id='oi:a',
                usage={'input_tokens': 999, 'output_tokens': 999},
                created_at=stamp,
                updated_at=stamp,
            )
        )
    for index, (key, input_tokens, output_tokens, origin) in enumerate(
        [('one', 10, 5, None), ('regen', 20, 7, None), ('tool', 3, 2, None), ('copy', 10, 5, 'one')]
    ):
        db.add(
            Generation(
                id='g' + key,
                user_id='u',
                chat_id='source',
                message_id='message',
                request_id=key,
                kind='assistant',
                status='completed',
                created_at=stamp * 1000,
            )
        )
        db.add(
            ModelCall(
                id=key,
                generation_id='g' + key,
                chat_id='source',
                sequence=index + 1,
                binding_id='oi:a',
                model_id='preset-a',
                status='completed',
                usage={
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    **({'archive_origin': {'call_id': origin}} if origin else {}),
                },
                pricing={},
                created_at=stamp * 1000,
            )
        )
    from open_webui.models.customization import CostSnapshot

    db.add(
        CostSnapshot(
            id='source-anchor',
            chat_id='source',
            message_id='message',
            max_sequence=4,
            baseline_ids=[],
            totals={},
            created_at=stamp * 1000,
        )
    )
    await db.flush()
    totals = await ChatMessages.get_token_usage_by_model(db=db)
    assert totals['oi:a']['input_tokens'] == 1032 and totals['oi:a']['output_tokens'] == 1013
    assert totals['oi:a']['message_count'] == 4  # three unique calls + one untracked historical message
    assert (await ChatMessages.get_user_top_models('u', stamp - 1, stamp + 1, db=db))[0]['messages'] == 4
    summary = await ChatMessages.get_user_usage_summary('u', db=db)
    assert summary['total_tokens'] == 2045 and summary['messages'] == 4
    daily = await ChatMessages.get_user_daily_usage('u', stamp, stamp, db=db)
    assert daily[0]['tokens'] == 2045 and daily[0]['models'] == {'oi:a': 4}
    assert not await statistics.by(db, user_id='other')
    for grouping in [ChatMessages.get_daily_message_counts_by_model, ChatMessages.get_hourly_message_counts_by_model]:
        values = await grouping(stamp - 1, stamp + 1, db=db)
        assert sum(row.get('oi:a', 0) for row in values.values()) == 4
    # New v0.12.5 dashboard and interval endpoints must use the same canonical calls.
    dashboard = await ChatMessages.get_analytics_summary(db=db)
    assert dashboard == {'total_messages': 4, 'total_chats': 2, 'total_models': 1, 'total_users': 1}
    assert (await ChatMessages.get_model_analytics(db=db))['oi:a']['count'] == 4
    for granularity in ['10m', 'hourly', '3h', '6h', 'daily']:
        values = await ChatMessages.get_usage_over_time_by_model(
            stamp - 1, stamp + 1, db=db, granularity=granularity, metric='tokens', bucket_offset=86400
        )
        assert sum(row.get('oi:a', 0) for row in values.values()) == 2045
    await db.execute(delete(ChatMessage).where(ChatMessage.chat_id != 'legacy'))
    assert await ChatMessages.get_token_usage_by_model(db=db) == totals


@pytest.mark.asyncio
async def test_history_directory_does_not_disclose_guessed_private_model_identity(db):
    db.add(
        Connection(
            id='private',
            owner_id='other',
            name='Private connection',
            template='openai-compatible',
            protocol='openai-chat',
            base_url='https://synthetic.invalid',
            transport='server',
            credentials='',
            settings={},
            shared=False,
            enabled=True,
            revision=1,
            created_at=1,
            updated_at=1,
        )
    )
    db.add(
        ModelBinding(
            id='oi:private',
            connection_id='private',
            upstream_id='private-model',
            name='Private model',
            protocol='openai-chat',
            enabled=True,
            available=True,
            capabilities={},
            pricing={},
            params={'system': 'private'},
            aliases=[],
            updated_at=1,
        )
    )
    db.add(Chat(id='chat', user_id='u', title='synthetic', chat={}, created_at=1, updated_at=1))
    db.add(
        ChatMessage(
            id='guessed',
            chat_id='chat',
            user_id='u',
            role='assistant',
            model_id='oi:private',
            created_at=1,
            updated_at=1,
        )
    )
    await db.flush()
    assert await history.identities(db, SimpleNamespace(id='u', role='user')) == []
    safe = history.safe_descriptor(
        {
            'id': 'test',
            'name': 'Name',
            'params': {'secret': 'no'},
            'connection': {'id': 'c', 'name': 'C', 'credentials': 'no'},
        }
    )
    assert 'secret' not in str(safe) and 'credentials' not in str(safe)


@pytest.mark.asyncio
async def test_shared_display_snapshot_is_scoped_and_does_not_change_content(db, monkeypatch):
    source = {'history': {'messages': {'m': {'role': 'assistant', 'model': 'oi:a', 'content': 'unchanged'}}}}

    async def records(session, chat_id):
        assert chat_id == 'authorized-chat'
        return {
            'call': SimpleNamespace(
                created_at=1,
                model_id='oi:a',
                binding_id='oi:a',
                usage={
                    'context': {
                        'model_identity': {
                            'id': 'oi:a',
                            'name': 'Same',
                            'connection': {'id': 'a', 'name': 'Connection A', 'credentials': 'private'},
                            'params': {'secret': 'no'},
                        }
                    }
                },
            )
        }, []

    monkeypatch.setattr('open_webui.utils.oi.accounting.conversation_records', records)
    decorated = await history.decorate_conversation(db, 'authorized-chat', source)
    assert 'oi_model_identity' not in source['history']['messages']['m']
    message = decorated['history']['messages']['m']
    assert message['content'] == 'unchanged'
    assert message['oi_model_identity']['connection']['name'] == 'Connection A'
    assert 'credentials' not in str(message) and 'secret' not in str(message)
