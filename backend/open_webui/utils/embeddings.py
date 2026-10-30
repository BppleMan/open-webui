import logging
import random
import sys

from fastapi import Request
from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL, GLOBAL_LOG_LEVEL
from open_webui.models.models import Models
from open_webui.models.users import UserModel
from open_webui.routers.ollama import (
    GenerateEmbedForm,
)
from open_webui.routers.ollama import (
    embed as ollama_embed,
)
from open_webui.routers.openai import embeddings as openai_embeddings
from open_webui.utils.models import check_model_access
from open_webui.utils.payload import convert_embed_payload_openai_to_ollama
from open_webui.utils.response import convert_embedding_response_ollama_to_openai

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


async def generate_embeddings(
    request: Request,
    form_data: dict,
    user: UserModel,
    bypass_filter: bool = False,
):
    """
    Dispatch and handle embeddings generation based on the model type (OpenAI, Ollama).

    Args:
        request (Request): The FastAPI request context.
        form_data (dict): The input data sent to the endpoint.
        user (UserModel): The authenticated user.
        bypass_filter (bool): If True, disables access filtering (default False).

    Returns:
        dict: The embeddings response, following OpenAI API compatibility.
    """
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    # Attach extra metadata from request.state if present
    if hasattr(request.state, 'metadata'):
        if 'metadata' not in form_data:
            form_data['metadata'] = request.state.metadata
        else:
            form_data['metadata'] = {
                **form_data['metadata'],
                **request.state.metadata,
            }

    # If "direct" flag present, use only that model
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data.get('model')
    from open_webui.internal.db import get_async_db
    from open_webui.utils.oi.model_targets import resolve_native_target
    from open_webui.utils.oi import capability_calls

    async with get_async_db() as db:
        native = await resolve_native_target(db, model_id, user)
    if native is not None:
        return await capability_calls.embeddings(
            model_id,
            form_data.get('input'),
            user=user,
            extra={k: form_data[k] for k in ('dimensions', 'user') if k in form_data},
            public_response=True,
            encoding_format=form_data.get('encoding_format', 'float'),
        )
    if model_id not in models:
        raise Exception('Model not found')
    model = models[model_id]

    # Access filtering
    if not getattr(request.state, 'direct', False):
        if not bypass_filter and user.role == 'user':
            await check_model_access(user, model)

    # Ollama backend — use /api/embed which supports batch input natively
    if model.get('owned_by') == 'ollama':
        ollama_payload = convert_embed_payload_openai_to_ollama(form_data)
        response = await ollama_embed(
            request=request,
            form_data=GenerateEmbedForm(**ollama_payload),
            user=user,
        )
        return convert_embedding_response_ollama_to_openai(response)

    # Default: OpenAI or compatible backend
    return await openai_embeddings(
        request=request,
        form_data=form_data,
        user=user,
    )
