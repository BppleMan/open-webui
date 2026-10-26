import json
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from open_webui.models.customization import Connection, ModelBinding, ModelCall
from open_webui.models.models import Models
from open_webui.utils.oi import capability_calls, catalog, runtime


@pytest.mark.asyncio
async def test_parallel_retrieval_and_followup_keep_their_generation_and_model_binding(db, monkeypatch):
    from open_webui.retrieval.utils import get_embedding_function, get_reranking_function, RerankCompressor
    from langchain_core.documents import Document

    user = await fixtures(db, monkeypatch)
    lock = asyncio.Lock()

    @asynccontextmanager
    async def session():
        async with lock:
            yield db

    monkeypatch.setattr(runtime, 'get_async_db', session)
    monkeypatch.setattr(capability_calls, 'get_async_db', session)

    async def provider(request):
        await asyncio.sleep(0)
        payload = json.loads(request.content)
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(
                200,
                json={
                    'data': [{'index': i, 'embedding': [1.0, 0.1]} for i, _ in enumerate(payload['input'])],
                    'usage': {'prompt_tokens': 8},
                },
            )
        return httpx.Response(
            200,
            json={
                'results': [{'index': i, 'relevance_score': i + 0.1} for i, _ in enumerate(payload['documents'])],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 0},
            },
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        capability_calls.httpx,
        'AsyncClient',
        lambda **kwargs: client(**{**kwargs, 'transport': httpx.MockTransport(provider)}),
    )

    async def branch(name):
        metadata = {'chat_id': 'retrieval-' + name, 'message_id': name, 'oi_request_id': name}
        generation = await runtime.start_generation(user, metadata, 'oi:' + name)
        token = runtime.active_generation.set((user, metadata))
        try:
            embedding = get_embedding_function('connection', 'oi:' + name, None, '', '', 1)
            assert len(await embedding(['literal first', 'literal second'])) == 2
            rerank = get_reranking_function('connection', 'oi:' + name, None)
            compressor = RerankCompressor(
                embedding_function=embedding, reranking_function=lambda q, d: rerank(q, d), top_n=1, r_score=0
            )
            assert (
                await compressor.acompress_documents(
                    [Document(page_content='first'), Document(page_content='second')], 'literal query'
                )
            )[0].page_content == 'second'
        finally:
            runtime.active_generation.reset(token)
        await runtime.end_generation(generation)

    await asyncio.gather(branch('a'), branch('b'))
    calls = (await db.execute(select(ModelCall))).scalars().all()
    assert len(calls) == 6
    for name in ('a', 'b'):
        owned = [call for call in calls if call.chat_id == 'retrieval-' + name]
        assert len(owned) == 3 and {call.binding_id for call in owned} == {'oi:' + name}
        assert len({call.generation_id for call in owned}) == 1
        assert sorted(call.usage['context']['purpose'] for call in owned) == ['embedding', 'embedding', 'rerank']
    assert runtime.active_generation.get() is None


async def fixtures(db, monkeypatch, protocol='openai-chat'):
    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(capability_calls, 'get_async_db', session)
    monkeypatch.setattr(runtime, 'get_async_db', session)
    monkeypatch.setattr(Models, 'get_model_by_id', AsyncMock(return_value=None))
    for name in ['a', 'b']:
        db.add(
            Connection(
                id=name,
                owner_id='u',
                name=name,
                template='openai-compatible',
                protocol=protocol,
                base_url=f'https://{name}.invalid/v1',
                credentials=catalog.encrypt_credentials({'api_key': f'synthetic-{name}'}),
                settings={'auth': 'bearer'},
                shared=False,
                enabled=True,
                revision=1,
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            ModelBinding(
                id=f'oi:{name}',
                connection_id=name,
                upstream_id='same-model',
                name='Same',
                protocol=protocol,
                enabled=True,
                available=True,
                capabilities={
                    'embedding': True,
                    'tts': True,
                    'stt': True,
                    'rerank': True,
                    'realtime': True,
                    'image_generation': True,
                    'image_edit': True,
                },
                params={},
                pricing={'rates': {'input': '2'}},
                aliases=[],
                updated_at=1,
            )
        )
    await db.flush()
    return SimpleNamespace(id='u', role='admin')


@pytest.mark.asyncio
async def test_embedding_routes_same_model_on_distinct_connections_and_records_input_usage(db, monkeypatch):
    user = await fixtures(db, monkeypatch)
    requests = []

    def provider(request):
        requests.append((request.url.host, request.headers['authorization'], json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                'data': [{'index': 1, 'embedding': [3.0, 4.0]}, {'index': 0, 'embedding': [1.0, 2.0]}],
                'usage': {'prompt_tokens': 5, 'total_tokens': 5},
            },
        )

    transport = httpx.MockTransport(provider)
    for ref in ['oi:a', 'oi:b']:
        assert await capability_calls.embeddings(ref, ['first', 'second'], user, transport) == [[1.0, 2.0], [3.0, 4.0]]
    assert [(host, key) for host, key, _ in requests] == [
        ('a.invalid', 'Bearer synthetic-a'),
        ('b.invalid', 'Bearer synthetic-b'),
    ]
    assert all(body['model'] == 'same-model' and body['input'] == ['first', 'second'] for _, _, body in requests)
    calls = (await db.execute(select(ModelCall))).scalars().all()
    assert {call.connection_id for call in calls} == {'a', 'b'}
    assert all(
        call.usage['input_tokens'] == 5
        and call.estimated_usd == '0.00001'
        and call.usage['context']['purpose'] == 'embedding'
        for call in calls
    )


@pytest.mark.asyncio
async def test_capability_permission_and_disabled_connection_are_checked_before_dispatch(db, monkeypatch):
    user = await fixtures(db, monkeypatch)
    with pytest.raises(HTTPException) as denied:
        await capability_calls.embeddings('oi:a', ['text'], SimpleNamespace(id='other', role='user'))
    assert denied.value.status_code == 404
    (await db.get(Connection, 'a')).enabled = False
    with pytest.raises(HTTPException):
        await capability_calls.embeddings('oi:a', ['text'], user)
    assert not (await db.execute(select(ModelCall))).scalars().all()


@pytest.mark.asyncio
async def test_capability_429_is_not_retried_or_replaced(db, monkeypatch):
    user = await fixtures(db, monkeypatch)
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(429, headers={'retry-after': '20'}, json={'error': 'synthetic limited'})

    with pytest.raises(HTTPException) as limited:
        await capability_calls.rerank('oi:a', 'query', ['document'], user, httpx.MockTransport(provider))
    assert limited.value.status_code == 429 and limited.value.headers['Retry-After'] == '20'
    assert len(requests) == 1
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert call.status == 'failed' and call.actual_usd is None and call.estimated_usd is None


@pytest.mark.asyncio
async def test_gemini_embedding_request_preserves_batch_order_without_instructions(db, monkeypatch):
    user = await fixtures(db, monkeypatch, 'gemini')
    bodies = []

    def provider(request):
        assert request.url.path.endswith('/models/same-model:batchEmbedContents')
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                'embeddings': [{'values': [1.0, 2.0]}, {'values': [3.0, 4.0]}],
                'usageMetadata': {'promptTokenCount': 7},
            },
        )

    result = await capability_calls.embeddings('oi:a', ['literal1', 'literal2'], user, httpx.MockTransport(provider))
    assert result == [[1.0, 2.0], [3.0, 4.0]]
    assert [r['content'] for r in bodies[0]['requests']] == [
        {'parts': [{'text': 'literal1'}]},
        {'parts': [{'text': 'literal2'}]},
    ]


@pytest.mark.asyncio
async def test_invalid_embeddings_preserve_confirmed_usage_and_fail_the_call(db, monkeypatch):
    user = await fixtures(db, monkeypatch)
    with pytest.raises(HTTPException) as invalid:
        await capability_calls.embeddings(
            'oi:a',
            ['text'],
            user,
            httpx.MockTransport(
                lambda req: httpx.Response(
                    200, json={'data': [{'index': 0, 'embedding': []}], 'usage': {'prompt_tokens': 7}}
                )
            ),
        )
    assert invalid.value.status_code == 502
    call = (await db.execute(select(ModelCall))).scalar_one()
    assert call.status == 'failed' and call.usage['input_tokens'] == 7


@pytest.mark.asyncio
async def test_audio_uses_binding_model_and_keeps_binary_cost_unknown(db, monkeypatch):
    user = await fixtures(db, monkeypatch)
    requests = []

    def provider(request):
        requests.append(request)
        if request.url.path.endswith('/speech'):
            assert json.loads(request.content)['model'] == 'same-model'
            return httpx.Response(200, content=b'synthetic-audio', headers={'content-type': 'audio/mpeg'})
        assert b'name="model"\r\n\r\nsame-model' in request.content
        assert b'synthetic-wave' in request.content
        return httpx.Response(200, json={'text': 'synthetic transcript', 'usage': {'type': 'duration', 'seconds': 2}})

    transport = httpx.MockTransport(provider)
    speech = await capability_calls.speech('oi:b', {'input': 'hello', 'voice': 'synthetic'}, user, transport=transport)
    assert speech.body == b'synthetic-audio'
    transcript = await capability_calls.transcription('oi:a', 'test.wav', b'synthetic-wave', user, transport=transport)
    assert transcript['text'] == 'synthetic transcript'
    assert [r.url.host for r in requests] == ['b.invalid', 'a.invalid']
    assert all(call.estimated_usd is None for call in (await db.execute(select(ModelCall))).scalars())


@pytest.mark.asyncio
async def test_image_generation_and_edit_route_the_binding_and_preserve_usage(db, monkeypatch):
    user = await fixtures(db, monkeypatch)
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(
            200, json={'data': [{'b64_json': 'c3ludGhldGlj'}], 'usage': {'input_tokens': 4, 'output_tokens': 2}}
        )

    transport = httpx.MockTransport(provider)
    result, target = await capability_calls.images('oi:a', {'prompt': 'synthetic', 'n': 1}, user, transport=transport)
    assert result == ['c3ludGhldGlj'] and target.binding.id == 'oi:a'
    await capability_calls.images(
        'oi:b', {'prompt': 'edit'}, user, input_images=['data:image/png;base64,c3ludGhldGlj'], transport=transport
    )
    assert json.loads(requests[0].content)['model'] == 'same-model'
    assert requests[1].url.host == 'b.invalid' and requests[1].url.path.endswith('/images/edits')
    assert b'name="model"\r\n\r\nsame-model' in requests[1].content
    assert all(call.usage['input_tokens'] == 4 for call in (await db.execute(select(ModelCall))).scalars())


@pytest.mark.asyncio
async def test_async_reranking_survives_the_existing_user_wrapper():
    from langchain_core.documents import Document
    from open_webui.retrieval.utils import RerankCompressor

    async def ranks(query, documents):
        return [0.1, 0.9]

    compressor = RerankCompressor(
        embedding_function=None, reranking_function=lambda query, documents: ranks(query, documents), top_n=1, r_score=0
    )
    result = await compressor.acompress_documents(
        [Document(page_content='low'), Document(page_content='high')], 'query'
    )
    assert result[0].page_content == 'high'


@pytest.mark.asyncio
async def test_realtime_same_connection_and_per_response_ledger_deduplication(db, monkeypatch):
    from open_webui.utils.oi import realtime, accounting

    user = await fixtures(db, monkeypatch)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(realtime, 'get_async_db', session)
    config = {'audio.realtime.model': 'oi:a', 'audio.realtime.transcription_model': 'oi:b'}
    with pytest.raises(HTTPException):
        await realtime.connection(config, user, 'chat')
    config['audio.realtime.transcription_model'] = 'oi:a'
    url, headers, voice, transcription = await realtime.connection(config, user, 'chat')
    assert url == 'wss://a.invalid/v1/realtime?model=same-model'
    ledger = realtime.CallLedger(user, 'chat', voice, transcription)
    command = await ledger.prepare({'type': 'response.create', 'response': {'metadata': {'input_item_id': 'item'}}})
    assert (await db.execute(select(ModelCall))).scalar_one().status == 'pending'
    event = {
        'type': 'response.done',
        'response': {
            'id': 'resp1',
            'metadata': command['response']['metadata'],
            'status': 'incomplete',
            'status_details': {'reason': 'max_output_tokens'},
            'usage': {
                'input_tokens': 100,
                'output_tokens': 10,
                'input_token_details': {'audio_tokens': 80, 'text_tokens': 20, 'cached_tokens': 0},
                'output_token_details': {'audio_tokens': 5, 'text_tokens': 5},
            },
        },
    }
    await ledger.observe(event)
    await ledger.observe(event)
    await ledger.observe({'type': 'input_audio_buffer.committed', 'item_id': 'item'})
    await ledger.observe(
        {
            'type': 'conversation.item.input_audio_transcription.completed',
            'item_id': 'item',
            'usage': {'type': 'duration', 'seconds': 2},
        }
    )
    await ledger.close()
    calls = (await db.execute(select(ModelCall).order_by(ModelCall.sequence))).scalars().all()
    assert len(calls) == 2 and calls[0].status == 'incomplete'
    assert calls[0].usage['input_audio_tokens'] == 80 and calls[0].estimated_usd is None
    assert (await accounting.get_totals(db, 'chat'))['unknown_calls'] == 2


def test_audio_pricing_requires_explicit_modality_rates():
    from open_webui.utils.oi import accounting

    usage = accounting.normalize_usage(
        {
            'input_tokens': 100,
            'output_tokens': 20,
            'input_token_details': {
                'audio_tokens': 80,
                'cached_tokens': 30,
                'cached_tokens_details': {'audio_tokens': 20},
            },
            'output_token_details': {'audio_tokens': 10},
        },
        'realtime',
    )
    assert accounting.estimate(usage, {'rates': {'input': '1', 'output': '2', 'cache_read': '0.5'}}) is None
    assert (
        accounting.estimate(
            usage,
            {
                'rates': {
                    'input': '1',
                    'output': '2',
                    'cache_read': '0.5',
                    'input_audio': '10',
                    'output_audio': '20',
                    'cache_read_audio': '5',
                }
            },
        )
        == '0.000935'
    )
