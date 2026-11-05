"""Actual JWT/refresh/revocation code, synthetic account and in-memory Redis only."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.routers import auths
from open_webui.socket import main as socket
from open_webui.utils import auth, mfa
from starlette.requests import Request
from starlette.responses import Response


class RedisFixture:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value


def request(token, redis):
    return Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/api/v1/auths/refresh',
            'headers': [(b'authorization', ('Bearer ' + token).encode())],
            'app': SimpleNamespace(state=SimpleNamespace(redis=redis)),
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('renewed_session', [False, True])
@pytest.mark.parametrize('revoke', ['signout', 'account'])
async def test_explicit_logout_revokes_the_renewed_session(monkeypatch, renewed_session, revoke):
    redis = RedisFixture()
    account = SimpleNamespace(session_stamp='synthetic-stamp', mfa=None)
    monkeypatch.setattr(mfa, 'get_auth', AsyncMock(side_effect=lambda *args: account))
    monkeypatch.setattr(mfa, 'get_mfa_config', AsyncMock(return_value={}))
    monkeypatch.setattr(mfa, 'is_mfa_required', lambda *args: False)
    monkeypatch.setattr(socket, 'disconnect_user_sessions', AsyncMock())
    monkeypatch.setattr(auths.Config, 'get', AsyncMock(return_value='1h'))
    monkeypatch.setattr(auths, 'get_session_user', AsyncMock(return_value={'id': 'u', 'permissions': {}}))
    monkeypatch.setattr(auths.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='u')))
    monkeypatch.setattr(auths, 'publish_event', AsyncMock())
    claims = {
        'id': 'u',
        'typ': 'session',
        'session_stamp': 'synthetic-stamp',
        'auth_time': 100,
        'auth_method': 'password',
    }
    initial = auth.create_token(claims, timedelta(minutes=30))
    independent = auth.create_token({**claims, 'auth_time': 200}, timedelta(minutes=30))
    latest = initial
    if renewed_session:
        result = await auths.refresh_session(request(initial, redis), Response(), SimpleNamespace(id='u'), None)
        latest = result['token']
        assert auth.decode_token(latest)['auth_time'] == 100
    if revoke == 'signout':
        logout_response = Response()
        await auths.signout(request(latest, redis), logout_response, None)
        assert 'Max-Age=0' in logout_response.headers.get('set-cookie', '')
    else:
        account.session_stamp = 'revoked-synthetic-stamp'
    assert not await auth.is_valid_token(auth.decode_token(latest), redis)
    assert (await auth.is_valid_token(auth.decode_token(independent), redis)) is (revoke == 'signout')
    predecessor_valid = await auth.is_valid_token(auth.decode_token(initial), redis)
    renewed_after_logout = False
    try:
        result = await auths.refresh_session(request(initial, redis), Response(), SimpleNamespace(id='u'), None)
        renewed_after_logout = await auth.is_valid_token(auth.decode_token(result['token']), redis)
    except Exception as exc:
        assert getattr(exc, 'status_code', None) == 401
    assert not predecessor_valid and not renewed_after_logout, {
        'same_auth_time': 100,
        'initial_token_still_valid': predecessor_valid,
        'can_refresh_again_after_logout': renewed_after_logout,
        'unrelated_session_preserved': True,
    }


@pytest.fixture
def auth_environment(monkeypatch):
    class TimedRedis(RedisFixture):
        def __init__(self):
            super().__init__()
            self.now, self.expires = 0, {}

        async def get(self, key):
            if self.expires.get(key, float('inf')) <= self.now:
                return None
            return await super().get(key)

        async def set(self, key, value, **kwargs):
            await super().set(key, value)
            self.expires[key] = self.now + kwargs.get('ex', float('inf'))

    redis = TimedRedis()
    account = SimpleNamespace(session_stamp='stamp', mfa=SimpleNamespace(secret='fixture', reset_required=False))
    monkeypatch.setattr(mfa, 'get_auth', AsyncMock(return_value=account))
    monkeypatch.setattr(mfa, 'get_mfa_config', AsyncMock(return_value={}))
    monkeypatch.setattr(mfa, 'is_mfa_required', lambda *a: True)
    monkeypatch.setattr(socket, 'disconnect_user_sessions', AsyncMock())
    monkeypatch.setattr(auths.Config, 'get', AsyncMock(return_value='3h'))
    monkeypatch.setattr(auths, 'get_session_user', AsyncMock(return_value={'id': 'u'}))
    monkeypatch.setattr(auths.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='u')))
    monkeypatch.setattr(auths, 'publish_event', AsyncMock())
    return redis, {
        'id': 'u',
        'session_stamp': 'stamp',
        'typ': 'session',
        'auth_time': 100,
        'auth_method': 'password',
        'mfa_verified': True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('logout_index', [0, 1, 2])
@pytest.mark.parametrize('permanent', [False, True])
async def test_logout_revokes_all_refresh_siblings_without_expiring_the_tombstone(
    auth_environment, legacy, logout_index, permanent
):
    redis, claims = auth_environment
    original = auth.create_token(claims, None if permanent else timedelta(hours=1))
    if legacy:
        old = auth.decode_token(original)
        old.pop('sid')
        original = auth.jwt.encode(old, auth.SESSION_SECRET, algorithm=auth.ALGORITHM)
    independent = auth.create_token(claims, timedelta(hours=4))  # Same auth_time is still a distinct login.
    tokens = [original]
    for _ in range(2):
        tokens.append(
            (await auths.refresh_session(request(original, redis), Response(), SimpleNamespace(id='u'), None))['token']
        )
    decoded = [auth.decode_token(t) for t in tokens]
    assert len({d['jti'] for d in decoded}) == 3
    assert len({auth.login_session_id(d) for d in decoded}) == 1
    assert all(d['auth_time'] == 100 and d['mfa_verified'] for d in decoded)
    await auths.signout(request(tokens[logout_index], redis), Response(), None)
    redis.now = 7200  # The older per-token marker would already be gone.
    assert all([not await auth.is_valid_token(d, redis) for d in decoded])
    assert await auth.is_valid_token(auth.decode_token(independent), redis)


@pytest.mark.asyncio
async def test_logout_wins_while_refresh_waits_for_permissions(auth_environment, monkeypatch):
    import asyncio

    redis, claims = auth_environment
    initial = auth.create_token(claims, timedelta(hours=1))
    waiting, resume = asyncio.Event(), asyncio.Event()

    async def permissions(*args):
        waiting.set()
        await resume.wait()
        return {'id': 'u'}

    monkeypatch.setattr(auths, 'get_session_user', permissions)
    task = asyncio.create_task(
        auths.refresh_session(request(initial, redis), Response(), SimpleNamespace(id='u'), None)
    )
    await waiting.wait()
    await auths.signout(request(initial, redis), Response(), None)
    resume.set()
    with pytest.raises(Exception) as error:
        await task
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_socket_logout_is_scoped_to_the_login_family(monkeypatch):
    monkeypatch.setattr(socket, 'get_session_ids_by_user_ids', lambda ids: ['old', 'renewed', 'independent'])
    monkeypatch.setattr(
        socket,
        'SESSION_POOL',
        {
            'old': {'id': 'u', 'login_id': 'a'},
            'renewed': {'id': 'u', 'login_id': 'a'},
            'independent': {'id': 'u', 'login_id': 'b'},
        },
    )
    fake = SimpleNamespace(disconnect=AsyncMock(), emit=AsyncMock())
    monkeypatch.setattr(socket, 'sio', fake)
    await socket.disconnect_user_sessions('u', login_id='a')
    assert {a.args[0] for a in fake.disconnect.await_args_list} == {'old', 'renewed'}
    fake.disconnect.reset_mock()
    await socket.disconnect_user_sessions('u')
    assert fake.disconnect.await_count == 3


def test_voice_renewal_rejects_a_different_login_with_identical_authentication_time():
    from open_webui.routers.audio.realtime import renewed_human_session

    claims = {
        'id': 'u',
        'typ': 'session',
        'session_stamp': 'stamp',
        'auth_time': 100,
        'auth_method': 'password',
        'mfa_verified': True,
    }
    first = auth.decode_token(auth.create_token(claims, timedelta(hours=1)))
    assert renewed_human_session(first, auth.create_token(first, timedelta(hours=2)))
    assert not renewed_human_session(first, auth.create_token(claims, timedelta(hours=2)))
