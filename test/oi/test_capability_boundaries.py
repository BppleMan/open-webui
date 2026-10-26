from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from open_webui.utils.oi.model_capabilities import chat_capable, arena_candidates


def test_chat_and_arena_never_route_to_specialized_only_models():
    models = {
        'embed': {'id': 'embed', 'capabilities': {'embedding': True}},
        'chat': {'id': 'chat', 'capabilities': {'chat': True, 'image_generation': True}},
        'legacy': {'id': 'legacy'},
    }
    assert not chat_capable(models['embed']['capabilities'])
    assert arena_candidates(models) == ['chat', 'legacy']
    assert arena_candidates(models, ['legacy'], 'exclude') == ['chat']
    with pytest.raises(HTTPException) as blocked:
        arena_candidates(models, ['embed'])
    assert blocked.value.status_code == 422


@pytest.mark.asyncio
async def test_hybrid_429_propagates_without_second_provider_or_vector_fallback(monkeypatch):
    from open_webui.retrieval import utils
    from open_webui.models.config import Config

    error = HTTPException(429, 'synthetic reranker limited', headers={'Retry-After': '7'})
    monkeypatch.setattr(
        Config,
        'get_many',
        AsyncMock(
            return_value={
                'rag.enable_hybrid_search': True,
                'rag.top_k_reranker': 1,
                'rag.relevance_threshold': 0,
                'rag.hybrid_bm25_weight': 0.5,
                'rag.enable_hybrid_search_enriched_texts': False,
            }
        ),
    )
    hybrid = AsyncMock(side_effect=error)
    embedding = AsyncMock()
    monkeypatch.setattr(utils, 'query_collection_with_hybrid_search', hybrid)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(RERANKING_FUNCTION=None)))
    with pytest.raises(HTTPException) as rejected:
        await utils.query_collection(request, ['documents'], ['query'], embedding, 1)
    assert rejected.value is error and rejected.value.headers['Retry-After'] == '7'
    hybrid.assert_awaited_once()
    embedding.assert_not_called()


@pytest.mark.asyncio
async def test_native_hybrid_429_is_not_converted_to_legacy_retry(monkeypatch):
    from open_webui.retrieval import utils

    monkeypatch.setattr(utils, '_supports_native_hybrid_search', lambda: True)
    embedding = AsyncMock(side_effect=HTTPException(429, 'synthetic limit'))
    with pytest.raises(HTTPException):
        await utils.query_doc_with_native_hybrid_search('documents', 'query', embedding, 1, None, 1, 0, 0.5)
    embedding.assert_awaited_once()
