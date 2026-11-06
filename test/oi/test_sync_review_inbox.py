"""Real comment handlers/inbox/SQLite, no external event publication."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from open_webui.models.users import User, UserModel
from open_webui.models.models import Model
from open_webui.models.access_grants import AccessGrant
from open_webui.models.customization import Connection, ModelBinding
from open_webui.models.comments import CommentForm, CommentReplyForm
from open_webui.models.notifications import Notification
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.routers import comments as routes


@pytest.mark.asyncio
@pytest.mark.parametrize('resource_kind', ['official', 'oi'])
@pytest.mark.parametrize('operation', ['created', 'replied', 'mentioned'])
async def test_comment_notifies_accessible_resource_owner(db, monkeypatch, resource_kind, operation):
    for table in (User.__table__, Model.__table__, AccessGrant.__table__):
        await (await db.connection()).run_sync(lambda conn, t=table: t.create(conn, checkfirst=True))
    people = {}
    for uid in ['owner', 'author']:
        row = User(
            id=uid,
            email=uid + '@synthetic.invalid',
            role='user',
            name=uid,
            variables={},
            last_active_at=1,
            created_at=1,
            updated_at=1,
        )
        db.add(row)
        people[uid] = UserModel.model_validate(row)
    resource_id = 'official' if resource_kind == 'official' else 'oi:binding'
    if resource_kind == 'official':
        db.add(
            Model(
                id=resource_id,
                user_id='owner',
                name='Model',
                params={},
                meta={},
                is_active=True,
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            AccessGrant(
                id='grant',
                resource_type='model',
                resource_id=resource_id,
                principal_type='user',
                principal_id='*',
                permission='read',
                created_at=1,
            )
        )
    else:
        db.add(
            Connection(
                id='c',
                owner_id='owner',
                name='Shared connection',
                template='openai',
                protocol='openai-chat',
                transport='server',
                base_url='https://synthetic.invalid',
                credentials='',
                settings={},
                shared=True,
                enabled=True,
                revision=1,
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            ModelBinding(
                id=resource_id,
                connection_id='c',
                upstream_id='same',
                name='Model',
                protocol='openai-chat',
                capabilities={'chat': True},
                params={},
                pricing={},
                enabled=True,
                available=True,
                aliases=[],
                updated_at=1,
            )
        )
    await db.flush()
    monkeypatch.setattr(
        Config,
        'get',
        AsyncMock(side_effect=lambda key, *a, **k: key in {'comments.enable', 'notifications.enable', 'direct.enable'}),
    )
    monkeypatch.setattr(
        Config, 'get_many', AsyncMock(return_value={'comments.enable': True, 'notifications.enable': True})
    )
    monkeypatch.setattr(Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(routes, 'limit_write', AsyncMock())
    monkeypatch.setattr(routes, 'changed', AsyncMock())
    monkeypatch.setattr(routes, 'publish_event', AsyncMock())
    monkeypatch.setattr('open_webui.models.notifications.emit_changes', AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
    if operation == 'replied':
        parent = await routes.create_comment(
            request,
            CommentForm(resource_type='model', resource_id=resource_id, content='Original'),
            people['owner'],
            db,
        )
        result = await routes.reply(request, parent['id'], CommentReplyForm(content='Reply'), people['author'], db)
    else:
        result = await routes.create_comment(
            request,
            CommentForm(
                resource_type='model',
                resource_id=resource_id,
                content='<@U:owner|Owner> Please see this' if operation == 'mentioned' else 'Comment',
            ),
            people['author'],
            db,
        )
    assert result['resource_id'] == resource_id
    notifications = (await db.scalars(select(Notification).where(Notification.object_id == result['id']))).all()
    assert [(n.user_id, n.event_type) for n in notifications] == [('owner', 'comment.' + operation)]
    from open_webui.internal.db import Base

    await (await db.connection()).run_sync(lambda conn: Base.metadata.create_all(conn))
    from open_webui.utils.inbox import visible_clause, hydrate
    from sqlalchemy import func

    visible = await visible_clause(people['owner'], db)
    assert await db.scalar(select(func.count()).select_from(Notification).where(visible)) == 1
    shown = await hydrate(notifications, db)
    assert shown[0]['title'].endswith('Model') and shown[0]['preview']
    if resource_kind == 'oi':
        mention = await routes.create_comment(
            request,
            CommentForm(resource_type='model', resource_id=resource_id, content='<@U:author|Author> Synthetic mention'),
            people['owner'],
            db,
        )
        visible = await visible_clause(people['author'], db)
        assert await db.scalar(select(func.count()).select_from(Notification).where(visible)) == 1
        conn = await db.get(Connection, 'c')
        conn.shared = False
        await db.flush()
        # Even an official override grant must not expose a now-private native binding.
        db.add(
            Model(
                id=resource_id,
                user_id='owner',
                name='Model',
                params={},
                meta={},
                is_active=True,
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            AccessGrant(
                id='native-public-override',
                resource_type='model',
                resource_id=resource_id,
                principal_type='user',
                principal_id='*',
                permission='read',
                created_at=1,
            )
        )
        await db.flush()
        visible = await visible_clause(people['author'], db)
        assert await db.scalar(select(func.count()).select_from(Notification).where(visible)) == 0
        conn.enabled = False
        await db.flush()
        visible = await visible_clause(people['owner'], db)
        assert await db.scalar(select(func.count()).select_from(Notification).where(visible)) == 0
