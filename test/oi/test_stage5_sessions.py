from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from open_webui.utils.auth import create_token
from open_webui.routers.audio.realtime import renewed_human_session


def test_native_voice_renewal_requires_same_human_authentication():
    old = {
        'id': 'u',
        'typ': 'session',
        'session_stamp': 'stamp',
        'auth_time': 123,
        'auth_method': 'password',
        'mfa_verified': True,
    }

    def token(claims):
        return create_token(claims, timedelta(minutes=1))

    assert renewed_human_session(old, token(old))
    for change in [
        {'id': 'other'},
        {'session_stamp': 'revoked'},
        {'auth_time': 999},
        {'typ': 'automation'},
        {'mfa_verified': False},
        {'auth_method': 'oauth'},
    ]:
        assert not renewed_human_session(old, token(old | change))
    assert not renewed_human_session(old, create_token(old, timedelta(seconds=-1)))
    assert not renewed_human_session(old, None)


@pytest.mark.asyncio
async def test_socket_account_switch_disconnects_old_rooms(monkeypatch):
    from open_webui.socket import main

    fake = SimpleNamespace(
        get_environ=MagicMock(return_value={}), disconnect=AsyncMock(), save_session=AsyncMock(), enter_room=AsyncMock()
    )
    monkeypatch.setattr(main, 'sio', fake)
    monkeypatch.setattr(main, 'SESSION_POOL', {'sid': {'id': 'previous', 'chat_ids': ['private-chat']}})
    monkeypatch.setattr(main, 'get_verified_user_by_token', AsyncMock(return_value=SimpleNamespace(id='new')))
    await main.user_join('sid', {'auth': {'token': 'synthetic'}})
    fake.disconnect.assert_awaited_once_with('sid')
    fake.save_session.assert_not_awaited()
    fake.enter_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_socket_same_account_renewal_keeps_rooms_and_updates_credential(monkeypatch):
    from open_webui.socket import main

    fake = SimpleNamespace(
        get_environ=MagicMock(return_value={}), disconnect=AsyncMock(), save_session=AsyncMock(), enter_room=AsyncMock()
    )
    monkeypatch.setattr(main, 'sio', fake)
    pool = {'sid': {'id': 'same', 'chat_ids': ['own-chat']}}
    monkeypatch.setattr(main, 'SESSION_POOL', pool)
    user = SimpleNamespace(
        id='same',
        name='Synthetic',
        role='admin',
        model_dump=lambda **kw: {'id': 'same', 'name': 'Synthetic', 'role': 'admin'},
    )
    monkeypatch.setattr(main, 'get_verified_user_by_token', AsyncMock(return_value=user))
    monkeypatch.setattr(main.Config, 'get', AsyncMock(return_value=False))
    monkeypatch.setattr(main.Channels, 'get_channels_by_user_id', AsyncMock(return_value=[]))
    await main.user_join('sid', {'auth': {'token': 'renewed'}})
    assert pool['sid']['chat_ids'] == ['own-chat']
    assert fake.save_session.await_args.args[1]['token'] == 'renewed'
    fake.disconnect.assert_not_awaited()
