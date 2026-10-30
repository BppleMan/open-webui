"""Usage analytics count original calls, never copied conversation projections."""

from sqlalchemy import BigInteger, Integer, cast, case, func, literal, select, union_all
from open_webui.models.customization import ChatLedger, Generation, ModelCall


def records(user_id=None, start=None, end=None, group_id=None):
    from open_webui.models.chat_messages import ChatMessage

    origin = func.coalesce(ModelCall.usage['archive_origin']['call_id'].as_string(), ModelCall.id)
    calls = select(
        origin.label('event_id'),
        Generation.user_id.label('user_id'),
        ModelCall.chat_id.label('chat_id'),
        func.coalesce(ModelCall.binding_id, ModelCall.model_id).label('model_id'),
        cast(ModelCall.created_at / 1000, BigInteger).label('created_at'),
        cast(ModelCall.usage['input_tokens'].as_string(), BigInteger).label('input_tokens'),
        cast(ModelCall.usage['output_tokens'].as_string(), BigInteger).label('output_tokens'),
        func.row_number()
        .over(
            partition_by=origin,
            order_by=(case((ModelCall.id == origin, 0), else_=1), ModelCall.created_at, ModelCall.id),
        )
        .label('rank'),
    ).join(Generation, Generation.id == ModelCall.generation_id)
    if user_id is not None:
        calls = calls.where(Generation.user_id == user_id)
    if group_id:
        from open_webui.models.groups import group_user_memberships

        calls = calls.where(Generation.user_id.in_(select(group_user_memberships([group_id], True).c.user_id)))
    ranked = calls.subquery()
    columns = ['event_id', 'user_id', 'chat_id', 'model_id', 'created_at', 'input_tokens', 'output_tokens']
    canonical = select(*(ranked.c[key] for key in columns)).where(ranked.c.rank == 1)
    # Exclude only messages represented by calls, plus inherited projections.
    # A first new call must not erase the preceding untracked historical usage.
    legacy = select(
        (literal('legacy:') + ChatMessage.id).label('event_id'),
        ChatMessage.user_id,
        ChatMessage.chat_id,
        ChatMessage.model_id,
        ChatMessage.created_at,
        func.coalesce(
            cast(ChatMessage.usage['input_tokens'].as_string(), BigInteger),
            cast(ChatMessage.usage['prompt_tokens'].as_string(), BigInteger),
        ).label('input_tokens'),
        func.coalesce(
            cast(ChatMessage.usage['output_tokens'].as_string(), BigInteger),
            cast(ChatMessage.usage['completion_tokens'].as_string(), BigInteger),
        ).label('output_tokens'),
    ).where(
        ChatMessage.role == 'assistant',
        ChatMessage.model_id.isnot(None),
        ~select(Generation.id)
        .where(
            Generation.chat_id == ChatMessage.chat_id,
            (
                (Generation.message_id == ChatMessage.id)
                | (ChatMessage.chat_id + literal('-') + Generation.message_id == ChatMessage.id)
            ),
        )
        .exists(),
        ~ChatMessage.chat_id.in_(select(ChatLedger.chat_id).where(ChatLedger.inherited_snapshot_id.isnot(None))),
    )
    if user_id is not None:
        legacy = legacy.where(ChatMessage.user_id == user_id)
    if group_id:
        from open_webui.models.groups import group_user_memberships

        legacy = legacy.where(ChatMessage.user_id.in_(select(group_user_memberships([group_id], True).c.user_id)))
    combined = union_all(canonical, legacy).subquery()
    query = select(combined)
    if start is not None:
        query = query.where(combined.c.created_at >= start)
    if end is not None:
        query = query.where(combined.c.created_at <= end)
    return query.subquery()


async def by(db, key='model_id', user_id=None, start=None, end=None, group_id=None):
    r = records(user_id, start, end, group_id)
    result = await db.execute(
        select(
            r.c[key],
            func.count().label('count'),
            func.sum(case(((r.c.input_tokens.is_(None) | r.c.output_tokens.is_(None)), 1), else_=0)).label(
                'unknown_usage_calls'
            ),
            func.coalesce(func.sum(r.c.input_tokens), 0).label('input_tokens'),
            func.coalesce(func.sum(r.c.output_tokens), 0).label('output_tokens'),
            func.count(func.distinct(r.c.user_id)).label('unique_users'),
            func.count(func.distinct(r.c.chat_id)).label('unique_chats'),
        ).group_by(r.c[key])
    )
    return {row[key]: dict(row) for row in result.mappings()}
