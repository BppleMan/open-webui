from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.routers import auths
from open_webui.utils.auth import create_token, decode_token
from starlette.requests import Request
from starlette.responses import Response


def request_for(token):
    return Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/api/v1/auths/refresh',
            'headers': [(b'authorization', ('Bearer ' + token).encode())],
            'app': SimpleNamespace(state=SimpleNamespace(redis=None)),
        }
    )


@pytest.mark.asyncio
async def test_refresh_preserves_authentication_age_and_mfa(monkeypatch):
    async def config_get(key, *args, **kwargs):
        return '1h'

    async def valid(claims, redis):
        return claims['session_stamp'] == 'current'

    async def session_user(*args):
        return {'id': 'u', 'token': 'old', 'permissions': {'chat': True}}

    monkeypatch.setattr(auths.Config, 'get', config_get)
    monkeypatch.setattr(auths, 'is_valid_token', valid)
    monkeypatch.setattr(auths, 'get_session_user', session_user)
    old = create_token(
        {
            'id': 'u',
            'typ': 'session',
            'auth_time': 100,
            'auth_method': 'password',
            'mfa_verified': True,
            'session_stamp': 'current',
        },
        timedelta(minutes=1),
    )
    response = Response()
    result = await auths.refresh_session(request_for(old), response, SimpleNamespace(id='u'), None)
    renewed = decode_token(result['token'])
    assert renewed['auth_time'] == 100 and renewed['mfa_verified'] is True
    assert renewed['session_stamp'] == 'current'
    assert renewed['jti'] != decode_token(old)['jti']
    assert renewed['exp'] > decode_token(old)['exp']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=' in response.headers['set-cookie']


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['expired', 'revoked', 'automation', 'other_user'])
async def test_refresh_rejects_ineligible_sessions(monkeypatch, case):
    async def valid(claims, redis):
        return case != 'revoked'

    monkeypatch.setattr(auths, 'is_valid_token', valid)
    token = create_token(
        {'id': 'other' if case == 'other_user' else 'u', 'typ': 'automation' if case == 'automation' else 'session'},
        timedelta(seconds=-1 if case == 'expired' else 60),
    )
    with pytest.raises(HTTPException) as error:
        await auths.refresh_session(request_for(token), Response(), SimpleNamespace(id='u'), None)
    assert error.value.status_code == 401
