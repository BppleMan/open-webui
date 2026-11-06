"""SideChat must keep safe native replay even when the connection belongs to an admin."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.customization import Connection, Generation, ModelCall
from open_webui.models.shared_chats import SharedChat
from open_webui.models.access_grants import AccessGrant
from open_webui.models.groups import Groups
from open_webui.utils.oi.native_transfer import side_context
from open_webui.utils.oi.temporary_native import restore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'viewer,configuration_owner',
    [('owner', 'owner'), ('owner', 'provider-admin'), ('collaborator', 'provider-admin')],
    ids=['private-owner-control', 'shared-connection-owner', 'shared-chat-portable-control'],
)
async def test_side_context_preserves_signed_output_without_private_config(
    db, monkeypatch, viewer, configuration_owner
):
    for table in (SharedChat.__table__, AccessGrant.__table__):
        await (await db.connection()).run_sync(lambda conn, t=table: t.create(conn, checkfirst=True))
    native = {
        'call_id': 'call',
        'binding_id': 'oi:leaf',
        'protocol': 'gemini',
        'replayable': True,
        'caller_id': 'owner',
        'configuration_owner_id': configuration_owner,
        'body': {
            'private_request_options': 'PRIVATE_PROVIDER_CONFIGURATION',
            'candidates': [{'content': {'parts': [{'text': 'original', 'thoughtSignature': 'SIGNED_OUTPUT'}]}}],
        },
    }
    source = {
        'id': 'answer',
        'role': 'assistant',
        'user_id': 'owner',
        'model': 'oi:leaf',
        'content': 'original',
        'done': True,
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'original'}]}],
        'oi_native': [native],
    }
    body = {'history': {'currentId': 'answer', 'messages': {'answer': deepcopy(source)}}}
    chat = Chat(id='parent', user_id='owner', title='Synthetic', created_at=1, updated_at=1, chat=body)
    conn = Connection(
        id='c',
        owner_id=configuration_owner,
        name='Connection',
        template='gemini',
        protocol='gemini',
        transport='server',
        base_url='https://synthetic.invalid',
        credentials='',
        settings={},
        shared=configuration_owner != 'owner',
        enabled=True,
        revision=1,
        created_at=1,
        updated_at=1,
    )
    generation = Generation(
        id='generation',
        user_id='owner',
        chat_id='parent',
        message_id='answer',
        request_id='request',
        kind='assistant',
        status='completed',
        created_at=1,
    )
    call = ModelCall(
        id='call',
        generation_id='generation',
        chat_id='parent',
        sequence=1,
        binding_id='oi:leaf',
        connection_id='c',
        model_id='oi:leaf',
        status='completed',
        usage={},
        pricing={},
        created_at=1,
    )
    db.add_all([chat, conn, generation, call])
    if viewer == 'collaborator':
        db.add(
            SharedChat(
                id='share',
                chat_id='parent',
                user_id='owner',
                title='Synthetic',
                chat={'share_mode': 'continue'},
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            AccessGrant(
                id='grant',
                resource_type='shared_chat',
                resource_id='parent',
                principal_type='user',
                principal_id=viewer,
                permission='read',
                created_at=1,
            )
        )
    await db.flush()
    await ChatMessages.upsert_message('answer', 'parent', 'owner', source, db=db)
    monkeypatch.setattr(Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    requested = deepcopy(source)
    requested.pop('oi_native')
    before = deepcopy(chat.chat)
    patches = await side_context(
        db, SimpleNamespace(id=viewer, role='user'), 'side', [requested], source_chat_id='parent'
    )
    copied = {**requested, **patches[0]}
    calls = restore(copied, viewer, 'side')
    assert calls, (
        'Accessible unedited parent lost all native replay solely because the connection has a different owner'
    )
    assert 'SIGNED_OUTPUT' in str(calls)
    if configuration_owner != viewer:
        assert 'PRIVATE_PROVIDER_CONFIGURATION' not in str(calls)
        assert calls[0]['replay_projection'] is True
    assert chat.chat == before
    assert await db.scalar(select(func.count()).select_from(ModelCall)) == 1
    assert await db.get(Chat, 'side') is None
