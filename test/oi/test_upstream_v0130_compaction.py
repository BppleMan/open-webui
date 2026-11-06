"""Cache-friendly compaction makes at most one provider attempt and preserves attribution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from open_webui.utils import context_compaction as c, chat


@pytest.mark.asyncio
@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('failure', ['success', '429', 'error', 'empty'])
async def test_summary_has_one_attempt_and_preserves_metadata(monkeypatch, cached, failure):
    async def config(*keys):
        return {}

    monkeypatch.setattr(c.Config, 'get_many', config)
    monkeypatch.setattr(c, 'prompt_template', AsyncMock(side_effect=lambda prompt, user: prompt))
    monkeypatch.setattr(c, 'apply_params_to_form_data', lambda form, *args: form)
    calls = []

    async def generate(request, form_data, user, **kwargs):
        calls.append(form_data)
        if failure == '429':
            return JSONResponse({'error': 'limit'}, status_code=429, headers={'Retry-After': '7'})
        if failure == 'error':
            raise HTTPException(503, 'unavailable')
        return {'choices': [{'message': {'content': 'summary' if failure == 'success' else ''}}]}

    monkeypatch.setattr(chat, 'generate_chat_completion', generate)
    request = SimpleNamespace(state=SimpleNamespace(metadata={'chat_id': 'unrelated'}))
    user = SimpleNamespace(id='u', name='Synthetic')
    older = [{'role': 'user', 'content': 'older'}]
    recent = [{'role': 'user', 'content': 'unique recent'}]
    form = {'messages': older + recent} if cached else None
    args = (request, form, user, 'm', {'m': {}}, older, recent, None, '{{COMPACTED_MESSAGES}} {{RECENT_MESSAGES}}')
    if failure == 'success':
        assert await c._generate_summary(*args, metadata={'chat_id': 'parent', 'message_id': 'last'}) == 'summary'
    else:
        with pytest.raises(HTTPException) as error:
            await c._generate_summary(*args, metadata={'chat_id': 'parent', 'message_id': 'last'})
        assert error.value.status_code == {'429': 429, 'error': 503, 'empty': 502}[failure]
        if failure == '429':
            assert error.value.headers['Retry-After'] == '7'
    assert (
        len(calls) == 1 and calls[0]['metadata']['chat_id'] == 'parent' and calls[0]['metadata']['message_id'] == 'last'
    )
    assert calls[0]['metadata']['task'] == 'context_compaction'
