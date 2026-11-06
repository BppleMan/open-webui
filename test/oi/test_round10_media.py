"""Real Markdown image conversion; only the upload boundary is synthetic."""

import base64
import io
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image, PngImagePlugin
from open_webui.utils import middleware, files
from open_webui.utils.oi import protocols
from test.oi.test_round9_protocols import context
from test.oi.test_review_regressions import binding, connection
from test.oi.round10_helpers import text_response


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'cpa-agy', 'ollama'])
@pytest.mark.parametrize('enabled', [False, True])
async def test_markdown_image_storage_conversion_preserves_usable_history(monkeypatch, protocol, enabled):
    image_bytes = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text('fixture', 'synthetic image metadata ' * 60)
    Image.new('RGB', (2, 2), 'white').save(image_bytes, format='PNG', pnginfo=info)
    data = 'data:image/png;base64,' + base64.b64encode(image_bytes.getvalue()).decode()
    raw = 'A picture: ![image](' + data + ')'
    saved, events, ctx = context(monkeypatch, protocol)
    monkeypatch.setattr(middleware, 'ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION', enabled)
    upload = AsyncMock(return_value=(None, {'url': '/api/v1/files/synthetic-markdown/content'}))
    monkeypatch.setattr(files, 'upload_image', upload)
    response = await protocols.generate(
        ctx['request'],
        ctx['form_data'],
        ctx['user'],
        binding(protocol),
        connection(protocol),
        'markdown-image',
        httpx.MockTransport(lambda _: text_response(protocol, [raw])),
    )
    await middleware.process_chat_response(response, ctx)
    saved.update(id='reply', role='assistant', model='oi:leaf')
    assert upload.await_count == int(enabled), (saved, events)
    assert ('/api/v1/files/synthetic-markdown/content' in json.dumps(saved['output'])) is enabled
    error, payload = None, None
    try:
        normalized = middleware.sanitize_tool_pairs(middleware.process_messages_with_output([saved]))
        _, payload = protocols.build_request({'messages': normalized}, binding(protocol), connection(protocol))
    except Exception as exc:
        error = str(exc)
    assert error is None, error
