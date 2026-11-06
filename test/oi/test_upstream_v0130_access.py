"""Connection-scoped comments, subagent eligibility and historical bad parameter isolation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from open_webui.models.customization import Connection, ModelBinding
from open_webui.utils import comments, subagents
from open_webui.utils.oi import catalog


async def rows(db, transport='server', shared=False):
    c = Connection(
        id='c',
        owner_id='owner',
        name='Connection A',
        template='openai',
        protocol='openai-chat',
        transport=transport,
        base_url='https://synthetic.invalid',
        credentials='',
        settings={},
        shared=shared,
        enabled=True,
        revision=1,
        created_at=1,
        updated_at=1,
    )
    b = ModelBinding(
        id='oi:b',
        connection_id='c',
        upstream_id='same',
        name='Same name',
        protocol='openai-chat',
        capabilities={'chat': True},
        params={},
        pricing={},
        enabled=True,
        available=True,
        aliases=[],
        updated_at=1,
    )
    db.add_all([c, b])
    await db.flush()
    return c, b


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport,shared,actor,allowed',
    [
        ('server', False, 'owner', True),
        ('server', False, 'other', False),
        ('server', True, 'other', True),
        ('browser', False, 'owner', True),
        ('browser', True, 'other', False),
    ],
)
async def test_comment_binding_access_matches_connection_scope(db, monkeypatch, transport, shared, actor, allowed):
    c, b = await rows(db, transport, shared)
    monkeypatch.setattr(comments.Config, 'get', AsyncMock(return_value=True))
    import open_webui.utils.models as models

    monkeypatch.setattr(models, 'check_model_access', AsyncMock())
    user = SimpleNamespace(id=actor, role='user')
    if allowed:
        resource = await comments.comment_resource('model', b.id, user, db, request=None)
        assert resource.id == b.id and resource.user_id == 'owner'
    else:
        with pytest.raises(HTTPException) as error:
            await comments.comment_resource('model', b.id, user, db, request=None)
        assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('disabled', ['connection', 'binding', 'available', 'deleted', 'acl', 'global_browser'])
async def test_comment_does_not_bypass_disabled_or_restricted_binding(db, monkeypatch, disabled):
    c, b = await rows(db, 'browser' if disabled == 'global_browser' else 'server', True)
    if disabled == 'connection':
        c.enabled = False
    if disabled == 'binding':
        b.enabled = False
    if disabled == 'available':
        b.available = False
    if disabled == 'deleted':
        c.settings = {'deleted': True}
    await db.flush()
    monkeypatch.setattr(
        comments.Config,
        'get',
        AsyncMock(side_effect=lambda key, *a, **k: key != 'direct.enable' if disabled == 'global_browser' else True),
    )
    import open_webui.utils.models as models

    monkeypatch.setattr(
        models, 'check_model_access', AsyncMock(side_effect=HTTPException(403) if disabled == 'acl' else None)
    )
    with pytest.raises(HTTPException) as error:
        await comments.comment_resource('model', b.id, SimpleNamespace(id='owner', role='user'), db)
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [None, [], 'bad', {'effort': None}, {'effort': {'label': 'broken', 'options': []}}])
async def test_legacy_private_invalid_controls_do_not_break_catalog(db, monkeypatch, bad):
    c, b = await rows(db)
    b.params = {'model_controls': bad}
    await db.flush()
    from open_webui.models.config import Config

    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=True))
    result = await catalog.model_catalog(db, SimpleNamespace(id='other', role='user'), internal=True)
    assert result[0]['info']['params']['model_controls'] == {}
    assert b.params['model_controls'] == bad


@pytest.mark.asyncio
async def test_subagent_targets_preserve_connection_identity_and_eligibility(monkeypatch):
    server = {
        'id': 'oi:server',
        'name': 'Same',
        'connection': {'name': 'A', 'transport': 'server'},
        'capabilities': {'chat': True},
    }
    browser = {
        'id': 'oi:browser',
        'name': 'Same',
        'connection': {'name': 'B', 'transport': 'browser'},
        'capabilities': {'chat': True},
    }
    embedding = {'id': 'oi:embedding', 'name': 'Same', 'capabilities': {'embedding': True}}
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(MODELS={m['id']: m for m in [server, browser, embedding]}))
    )
    monkeypatch.setattr(subagents, 'get_filtered_models', AsyncMock(side_effect=lambda items, user: items))
    result = await subagents.get_subagent_models(
        request,
        SimpleNamespace(id='owner'),
        {'info': {'meta': {'subagents': {'model_ids': list(request.app.state.MODELS)}}}},
    )
    assert result == [{'id': 'oi:server', 'name': 'A / Same', 'description': ''}]
