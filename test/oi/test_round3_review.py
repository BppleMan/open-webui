"""Third-round independent review probes; synthetic data, no network/provider calls."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import httpx
from sqlalchemy import select

import pytest
from open_webui.models.chats import Chat, Chats
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.shared_chats import SharedChat
from open_webui.models.customization import ModelCall
from open_webui.utils.oi import accounting, archive, statistics, runtime
from open_webui.utils.oi.protocols import generate, build_request
from open_webui.routers.chats import shared_chat_response
from test.oi.test_review_regressions import binding, connection
from test.oi.test_accounting import answer


@pytest.mark.asyncio
async def test_authorized_shared_chat_writer_can_reserve_generation(db, monkeypatch):
    from open_webui.models import chats as chat_models

    conn = await db.connection()
    await conn.run_sync(lambda sync: SharedChat.__table__.create(sync, checkfirst=True))
    db.add(Chat(id='shared-chat', user_id='owner', title='Shared', chat={}, created_at=1, updated_at=1))
    db.add(
        SharedChat(
            id='share',
            chat_id='shared-chat',
            user_id='owner',
            title='Shared',
            chat={'share_mode': 'continue'},
            created_at=1,
            updated_at=1,
        )
    )
    await accounting.begin_generation(db, 'owner', 'shared-chat', 'first', 'owner-request')
    await db.commit()
    user = SimpleNamespace(id='collaborator', role='user')
    monkeypatch.setattr(chat_models.AccessGrants, 'has_access', AsyncMock(return_value=True))
    assert await Chats.get_accessible_chat_by_id('shared-chat', user, permission='write', db=db)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    result, error = None, None
    try:
        result = await runtime.reserve_generations(
            user,
            {'chat_id': 'shared-chat', 'oi_request_id': 'collab-request'},
            [{'model_id': 'model', 'message_id': 'collab-reply'}],
        )
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    assert result is not None, {'write_access': True, 'reservation_error': error}


async def legacy_chat(db, chat_id='legacy'):
    message = {
        'id': 'reply',
        'role': 'assistant',
        'content': 'Old response',
        'model': 'model',
        'parentId': None,
        'childrenIds': [],
        'done': True,
        'usage': {'input_tokens': 100, 'output_tokens': 20},
    }
    body = {'models': ['model'], 'history': {'currentId': 'reply', 'messages': {'reply': message}}}
    db.add(Chat(id=chat_id, user_id='u', title='Legacy', chat=body, created_at=1, updated_at=1))
    await db.flush()
    await ChatMessages.upsert_message('reply', chat_id, 'u', message, db=db)
    return body


@pytest.mark.asyncio
async def test_legacy_archive_restore_does_not_turn_unknown_cost_into_zero(db):
    await legacy_chat(db)
    manifest = await archive.export_records(db, 'u', 'legacy')
    assert not manifest['billing']['ledgers']
    await archive.restore_records(db, 'u', manifest, 'restored')
    totals = await accounting.get_totals(db, 'restored')
    assert totals['unknown_baselines'] == 1, totals


@pytest.mark.asyncio
async def test_legacy_archive_repeated_restore_does_not_duplicate_statistics(db):
    await legacy_chat(db)
    before = await statistics.by(db)
    manifest = await archive.export_records(db, 'u', 'legacy')
    await archive.restore_records(db, 'u', manifest, 'restored')
    await archive.restore_records(db, 'u', manifest, 'restored-again')
    after = await statistics.by(db)
    assert after['model']['input_tokens'] == before['model']['input_tokens'], {'before': before, 'after': after}


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_temporary_ollama_response_does_not_persist_reply_in_cost_ledger(db, monkeypatch, stream):
    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', AsyncMock())
    user = SimpleNamespace(id='u')
    metadata = {'chat_id': 'temporary:socket', 'oi_temporary_id': 'local-only', 'message_id': 'response'}
    await runtime.start_generation(user, metadata, 'oi:leaf')
    request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
    form = {
        'model': 'oi:leaf',
        'messages': [{'role': 'user', 'content': 'hello'}],
        'stream': stream,
        'metadata': metadata,
    }
    b, c = binding('ollama'), connection('ollama')
    terminal = {
        'model': 'model',
        'message': {'role': 'assistant', 'content': 'private-temporary-reply', 'thinking': 'private-temporary-thought'},
        'done': True,
        'done_reason': 'stop',
        'prompt_eval_count': 10,
        'eval_count': 3,
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=json.dumps(terminal) + '\n'))
    response = await runtime.track_call(
        request, form, user, lambda call_id: generate(request, form, user, b, c, call_id, transport), b, c
    )
    if stream:
        async for _ in response.body_iterator:
            pass
    call = (await db.execute(select(ModelCall))).scalar_one()
    encoded = json.dumps(call.usage)
    assert 'private-temporary-' not in encoded, call.usage


@pytest.mark.parametrize('modality', ['IMAGE', 'AUDIO'])
def test_gemini_modality_rates_are_applied_to_reported_modal_tokens(modality):
    raw = {
        'promptTokenCount': 10,
        'candidatesTokenCount': 100,
        'promptTokensDetails': [{'modality': 'TEXT', 'tokenCount': 10}],
        'candidatesTokensDetails': [{'modality': modality, 'tokenCount': 100}],
    }
    usage = accounting.normalize_usage(raw, 'gemini')
    pricing = {'rates': {'input': '1', 'output': '2', f'output_{modality.lower()}': '100'}}
    estimate = accounting.estimate(usage, pricing)
    assert estimate == '0.01001', {'usage': usage, 'estimated_usd': estimate}


def test_gemini_image_without_image_rate_remains_unknown():
    usage = accounting.normalize_usage(
        {
            'promptTokenCount': 10,
            'candidatesTokenCount': 100,
            'candidatesTokensDetails': [{'modality': 'IMAGE', 'tokenCount': 100}],
        },
        'gemini',
    )
    assert accounting.estimate(usage, {'rates': {'input': '1', 'output': '2'}}) is None, usage


@pytest.mark.asyncio
async def test_shared_chat_does_not_expose_native_provider_instructions(db):
    _, payload = build_request(
        {
            'messages': [{'role': 'user', 'content': 'hello'}],
            'provider_options': {'instructions': 'private-owner-system-instructions'},
        },
        binding('openai-responses'),
        connection('openai-responses'),
    )
    assert payload['instructions'] == 'private-owner-system-instructions'
    message = {
        'id': 'reply',
        'role': 'assistant',
        'content': 'Visible reply',
        'parentId': None,
        'childrenIds': [],
        'model': 'oi:leaf',
        'done': True,
        'user_id': 'owner',
        'oi_native': [
            {
                'call_id': 'call',
                'binding_id': 'oi:leaf',
                'protocol': 'openai-responses',
                'body': {
                    'id': 'response',
                    'status': 'completed',
                    'instructions': 'private-owner-system-instructions',
                    'output': [
                        {
                            'type': 'message',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': 'Visible reply'}],
                        }
                    ],
                },
            }
        ],
    }
    body = {
        'params': {'provider_options': {'instructions': 'private-owner-system-instructions'}},
        'history': {'currentId': 'reply', 'messages': {'reply': message}},
    }
    row = Chat(id='shared-native', user_id='owner', title='Shared', chat=body, created_at=1, updated_at=1)
    db.add(row)
    await db.commit()
    shared = await shared_chat_response(row, SimpleNamespace(id='viewer', role='user'), db=db)
    assert 'params' not in shared['chat']  # Existing deliberate private-settings boundary.
    assert 'private-owner-system-instructions' not in json.dumps(shared), shared['chat']['history']['messages']['reply']


@pytest.mark.parametrize(
    'key,native', [('frequency_penalty', 'frequencyPenalty'), ('presence_penalty', 'presencePenalty')]
)
def test_existing_sampling_controls_map_to_native_gemini_fields(key, native):
    from open_webui.utils.payload import apply_model_params_to_body_openai

    form = apply_model_params_to_body_openai(
        {key: 0.5}, {'model': 'oi:leaf', 'messages': [{'role': 'user', 'content': 'hello'}]}
    )
    _, payload = build_request(form, binding('gemini'), connection('gemini'))
    assert key not in payload and payload['generationConfig'].get(native) == 0.5, payload


@pytest.mark.asyncio
async def test_shared_first_writer_keeps_chat_owner_and_call_actor(db, monkeypatch):
    from open_webui.models.customization import ChatLedger, Generation

    db.add(Chat(id='shared', user_id='owner', title='Shared', chat={}, created_at=1, updated_at=1))
    await db.commit()
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=await db.get(Chat, 'shared')))

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    for uid in ['writer', 'owner']:
        result = await runtime.reserve_generations(
            SimpleNamespace(id=uid, role='user'),
            {'chat_id': 'shared', 'oi_request_id': uid},
            [{'message_id': uid, 'model_id': 'model'}],
        )
        gen = await db.get(Generation, result[uid])
        call, _ = await accounting.begin_call(db, gen, 'model')
        await accounting.settle_call(db, call.id, {'input_tokens': 1, 'output_tokens': 1}, actual_usd='0.03')
        await accounting.finish_generation(db, gen.id)
        assert gen.user_id == uid
    assert (await db.get(ChatLedger, 'shared')).user_id == 'owner'
    assert (await accounting.get_totals(db, 'shared'))['actual_usd'] == '0.06'
    assert (await statistics.by(db, user_id='writer'))['model']['count'] == 1
    assert (await statistics.by(db, user_id='owner'))['model']['count'] == 1
    manifest = await archive.export_records(db, 'owner', 'shared')
    assert len(manifest['billing']['calls']) == 2
    await archive.restore_records(db, 'owner', manifest, 'copy')
    assert (await statistics.by(db))['model']['count'] == 2
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=None))
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await runtime.reserve_generations(
            SimpleNamespace(id='stranger', role='user'), {'chat_id': 'shared'}, [{'message_id': 'denied'}]
        )
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_ledger_owner_check_is_not_removed_for_nonchat_ledgers(db):
    await accounting.begin_generation(db, 'owner', 'temporary:private', 'm', 'first')
    with pytest.raises(PermissionError):
        await accounting.begin_generation(db, 'intruder', 'temporary:private', 'm2', 'second')


@pytest.mark.asyncio
async def test_old_shared_ledger_owner_is_repaired_from_authoritative_chat(db, monkeypatch):
    from open_webui.models.customization import ChatLedger

    db.add(Chat(id='shared', user_id='owner', title='Shared', chat={}, created_at=1, updated_at=1))
    db.add(ChatLedger(chat_id='shared', user_id='old-collaborator', sequence=0))
    await db.flush()
    ledger = await accounting.ensure_ledger(db, 'owner', 'shared')
    assert ledger.user_id == 'owner'
    with pytest.raises(PermissionError):
        await accounting.ensure_ledger(db, 'old-collaborator', 'shared')


@pytest.mark.asyncio
async def test_shared_projection_preserves_visible_content_and_owner_native(db):
    native = [{'protocol': 'openai-responses', 'body': {'instructions': 'private instructions'}}]
    message = {
        'id': 'm',
        'role': 'assistant',
        'content': 'visible',
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'visible'}]}],
        'oi_native': native,
        'oi_native_state': 'private capsule',
    }
    row = Chat(
        id='share',
        user_id='owner',
        title='Shared',
        chat={'history': {'currentId': 'm', 'messages': {'m': message}}, 'messages': [message]},
        created_at=1,
        updated_at=1,
    )
    db.add(row)
    await db.flush()
    for user in [None, SimpleNamespace(id='viewer', role='user')]:
        result = await shared_chat_response(row, user, db)
        assert 'oi_native' not in json.dumps(result)
        assert result['chat']['history']['messages']['m']['content'] == 'visible'
    result = await shared_chat_response(row, SimpleNamespace(id='owner', role='user'), db)
    assert result['chat']['history']['messages']['m']['oi_native'] == native
    assert row.chat['history']['messages']['m']['oi_native'] == native


def test_gemini_modal_cache_is_separately_priced():
    raw = {
        'promptTokenCount': 110,
        'candidatesTokenCount': 10,
        'cachedContentTokenCount': 50,
        'promptTokensDetails': [{'modality': 'TEXT', 'tokenCount': 10}, {'modality': 'IMAGE', 'tokenCount': 100}],
        'cacheTokensDetails': [{'modality': 'IMAGE', 'tokenCount': 50}],
        'candidatesTokensDetails': [{'modality': 'TEXT', 'tokenCount': 10}],
    }
    usage = accounting.normalize_usage(raw, 'gemini')
    assert usage['cache_read_image_tokens'] == 50
    assert (
        accounting.estimate(
            usage,
            {'rates': {'input': '1', 'input_image': '100', 'output': '2', 'cache_read': '2', 'cache_read_image': '10'}},
        )
        == '0.00553'
    )


@pytest.mark.parametrize(
    'details', [None, [{'modality': 'IMAGE', 'tokenCount': 5}], [{'modality': 'VIDEO', 'tokenCount': 100}]]
)
def test_gemini_missing_or_incomplete_modality_classification_stays_unknown(details):
    raw = {'promptTokenCount': 10, 'candidatesTokenCount': 100}
    if details is not None:
        raw['candidatesTokensDetails'] = details
    usage = accounting.normalize_usage(raw, 'gemini')
    assert accounting.estimate(usage, {'rates': {'input': '1', 'output': '2', 'output_image': '100'}}) is None


@pytest.mark.asyncio
async def test_legacy_archive_unknown_baseline_is_editable_and_survives_reexport(db):
    await legacy_chat(db)
    manifest = await archive.export_records(db, 'u', 'legacy')
    await archive.restore_records(db, 'u', manifest, 'restored')
    base = await accounting.set_baseline(db, 'u', 'restored', 'imported-history', actual_usd='0', revision=1)
    assert base.actual_usd == '0'
    assert not (await accounting.get_totals(db, 'restored'))['incomplete']
    again = await archive.export_records(db, 'u', 'restored')
    await archive.restore_records(db, 'u', again, 'twice')
    assert not (await accounting.get_totals(db, 'twice'))['incomplete']
    assert (await statistics.by(db))['model']['input_tokens'] == 100


def test_gemini_incompatible_control_is_rejected_before_dispatch():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        build_request({'messages': [], 'repeat_penalty': 1.1}, binding('gemini'), connection('gemini'))
    assert error.value.status_code == 422


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('mime', ['image/png', 'audio/wav'])
def test_known_gemini_media_without_modality_counts_is_not_text_priced(stream, mime):
    from open_webui.utils.oi.protocols import NativeStream

    body = {
        'candidates': [
            {
                'content': {'role': 'model', 'parts': [{'inlineData': {'mimeType': mime, 'data': 'c3ludGhldGlj'}}]},
                'finishReason': 'STOP',
            }
        ],
        'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 100},
    }
    state = NativeStream('gemini')
    event = state.feed(body)[-1] if stream else state.nonstream(body)
    usage = accounting.normalize_usage(event['oi_usage'], 'gemini')
    assert accounting.estimate(usage, {'rates': {'input': '1', 'output': '2'}}) is None
    assert '_oi_missing_modality_breakdown' not in state.body['usageMetadata']


@pytest.mark.asyncio
async def test_gemini_input_media_missing_classification_remains_unknown(monkeypatch):
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', AsyncMock())
    form = {
        'model': 'oi:leaf',
        'stream': False,
        'messages': [
            {
                'role': 'user',
                'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,c3ludGhldGlj'}}],
            }
        ],
    }
    body = {
        'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'Visible'}]}, 'finishReason': 'STOP'}],
        'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 5},
    }
    response = await generate(
        SimpleNamespace(state=SimpleNamespace()),
        form,
        SimpleNamespace(id='u'),
        binding('gemini'),
        connection('gemini'),
        'call',
        httpx.MockTransport(lambda req: httpx.Response(200, json=body)),
    )
    data = response if isinstance(response, dict) else json.loads(response.body)
    usage = accounting.normalize_usage(data['oi_usage'], 'gemini')
    assert accounting.estimate(usage, {'rates': {'input': '1', 'output': '2'}}) is None
