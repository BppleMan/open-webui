from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from open_webui.utils.oi import realtime
from open_webui.models.customization import ModelCall


@pytest.mark.asyncio
async def test_realtime_partial_usage_survives_disconnect_without_double_count(db, monkeypatch):
    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(realtime, 'get_async_db', session)
    target = SimpleNamespace(
        binding=SimpleNamespace(id='oi:voice', pricing={}),
        connection=SimpleNamespace(id='c', template='openai-compatible', settings={}),
    )
    ledger = realtime.CallLedger(SimpleNamespace(id='u'), 'chat', target, target)
    command = await ledger.prepare({'type': 'response.create'})
    response = {'id': 'r', 'metadata': command['response']['metadata'], 'usage': {'input_tokens': 123}}
    await ledger.observe({'type': 'response.created', 'response': response})
    await ledger.observe({'type': 'response.updated', 'response': {**response, 'usage': {'output_tokens': 5}}})
    await ledger.close()
    await ledger.close()
    calls = (await db.execute(select(ModelCall))).scalars().all()
    assert len(calls) == 1 and calls[0].usage['input_tokens'] == 123 and calls[0].usage['output_tokens'] == 5
    assert calls[0].status == 'interrupted' and calls[0].actual_usd is None


@pytest.mark.asyncio
@pytest.mark.parametrize('revoke', ['session', 'call_permission', 'voice', 'stt', 'chat'])
async def test_realtime_every_command_validation_covers_all_revocation_sources(monkeypatch, revoke):
    from open_webui.routers.audio import realtime as bridge
    from open_webui.utils.oi import capability_calls, catalog

    user = SimpleNamespace(id='u', role='user')
    monkeypatch.setattr(
        bridge, 'get_verified_user_by_token', AsyncMock(return_value=None if revoke == 'session' else user)
    )
    monkeypatch.setattr(bridge.Config, 'get', AsyncMock(return_value=True))
    monkeypatch.setattr(bridge, 'has_permission', AsyncMock(return_value=revoke != 'call_permission'))
    monkeypatch.setattr(bridge.Models, 'get_model_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(bridge, 'BYPASS_MODEL_ACCESS_CONTROL', False)
    monkeypatch.setattr(
        bridge, 'check_model_access', AsyncMock(side_effect=HTTPException(403, 'revoked') if revoke == 'chat' else None)
    )
    connection = SimpleNamespace(id='c', base_url='https://synthetic.invalid')

    def target(id):
        return SimpleNamespace(
            binding=SimpleNamespace(id=id, upstream_id=id, protocol='openai-chat'), connection=connection
        )

    voice, stt = target('voice'), target('stt')

    async def resolve(ref, cap, user):
        if ref == revoke:
            raise HTTPException(403, 'revoked')
        return (voice if ref == 'voice' else stt), user

    monkeypatch.setattr(capability_calls, 'target_for', resolve)
    monkeypatch.setattr(catalog, 'credentials', lambda _: {})
    with pytest.raises((ValueError, HTTPException)):
        await bridge.validate_bridge_access(
            SimpleNamespace(state=SimpleNamespace(MODELS={'chat': {'id': 'chat'}})),
            'synthetic',
            None,
            'chat',
            SimpleNamespace(voice=voice, transcription=stt),
        )
