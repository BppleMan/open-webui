"""Native Responses HTTP-200 failed terminal output is not a successful summary."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx, pytest
from fastapi import HTTPException
from open_webui.utils import context_compaction as c, chat
from open_webui.utils.oi import protocols
from test.oi.test_review_regressions import binding, connection


@pytest.fixture
def summary_env(monkeypatch):
    monkeypatch.setattr(c.Config, 'get_many', AsyncMock(return_value={}))
    monkeypatch.setattr(c, 'prompt_template', AsyncMock(side_effect=lambda prompt, user: prompt))
    monkeypatch.setattr(c, 'apply_params_to_form_data', lambda form, *args: form)
    return SimpleNamespace(state=SimpleNamespace(metadata={})), SimpleNamespace(id='u', role='admin', name='Synthetic')


def install_native_dispatch(monkeypatch, status):
    calls = []
    body = {
        'id': 'synthetic-summary',
        'status': status,
        'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'PARTIAL-SUMMARY'}]}
        ],
        'usage': {'input_tokens': 10, 'output_tokens': 4},
    }
    if status == 'failed':
        body['error'] = {'code': 'server_error', 'message': 'synthetic failure after partial text'}

    async def dispatch(request, form_data, user, **kwargs):
        calls.append(copy.deepcopy(form_data))
        return await protocols.generate(
            request,
            form_data,
            user,
            binding('openai-responses'),
            connection('openai-responses'),
            'summary-' + str(len(calls)),
            httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        )

    monkeypatch.setattr(chat, 'generate_chat_completion', dispatch)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('status', ['completed', 'failed'])
async def test_partial_failed_summary_is_rejected(summary_env, monkeypatch, cached, status):
    request, user = summary_env
    calls = install_native_dispatch(monkeypatch, status)
    older = [{'role': 'user', 'content': 'older unique'}]
    recent = [{'role': 'user', 'content': 'latest unique'}]
    form = {'messages': older + recent} if cached else None
    args = (request, form, user, 'oi:leaf', {'oi:leaf': {}}, older, recent, None, 'summarize')
    if status == 'failed':
        with pytest.raises(HTTPException):
            await c._generate_summary(*args)
    else:
        assert await c._generate_summary(*args) == 'PARTIAL-SUMMARY'
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['completed', 'failed'])
async def test_manual_compaction_writes_checkpoint_only_after_success(summary_env, monkeypatch, status):
    request, user = summary_env
    calls = install_native_dispatch(monkeypatch, status)
    messages = {
        'u1': {'id': 'u1', 'parentId': None, 'role': 'user', 'content': 'original history must remain'},
        'u2': {'id': 'u2', 'parentId': 'u1', 'role': 'user', 'content': 'latest question'},
    }
    current = SimpleNamespace(
        id='saved', current_message_id='u2', chat={'history': {'currentId': 'u2', 'messages': messages}}
    )
    monkeypatch.setattr(c, '_load_config', AsyncMock(return_value={'enable': True, 'prompt_template': 'summarize'}))
    monkeypatch.setattr(c.Chats, 'get_messages_map_by_chat_id', AsyncMock(return_value=messages))
    writes = []

    async def save(cid, mid, update, **kwargs):
        writes.append(copy.deepcopy(update))
        messages[mid].update(update)

    monkeypatch.setattr(c.Chats, 'upsert_message_to_chat_by_id_and_message_id', save)
    error = None
    try:
        await c.compact_chat_branch(request, user, current, 'oi:leaf', {'oi:leaf': {}})
    except HTTPException as exc:
        error = exc
    if status == 'failed':
        assert not writes, {'writes': writes, 'after': c._apply_latest_summary_checkpoint(list(messages.values()))}
        assert error is not None
    else:
        assert writes == [{'contextSummary': 'PARTIAL-SUMMARY'}]
    assert len(calls) == 1
