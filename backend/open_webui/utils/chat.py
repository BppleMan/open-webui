import asyncio
import copy
import logging
import random
import sys
import uuid
from typing import Any

from fastapi import HTTPException, Request
from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL, GLOBAL_LOG_LEVEL
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.functions import generate_function_chat_completion
from open_webui.internal.db import get_async_db
from open_webui.models.models import Models
from open_webui.models.users import UserModel
from open_webui.routers.ollama import (
    generate_chat_completion as generate_ollama_chat_completion,
)
from open_webui.routers.openai import (
    generate_chat_completion as generate_openai_chat_completion,
)
from open_webui.routers.pipelines import (
    process_pipeline_outlet_filter,
)
from open_webui.socket.main import (
    EVENT_QUEUES,
    get_event_call,
    get_event_emitter,
)
from open_webui.utils.filter import (
    get_filter_functions,
    process_filter_functions,
)
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.models import check_model_access, get_all_models
from open_webui.utils.oi.runtime import track_call
from open_webui.utils.payload import (
    apply_model_params_to_body_openai,
    apply_system_prompt_to_body,
    convert_payload_openai_to_ollama,
)
from open_webui.utils.response import (
    convert_response_ollama_to_openai,
    convert_streaming_response_ollama_to_openai,
)
from starlette.responses import StreamingResponse

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


# When the question has been asked, let silence not be the
# answer. But if the answer must wait, let it come honest.
async def generate_direct_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
    models: dict,
):
    log.info('generate_direct_chat_completion')

    metadata = form_data.pop('metadata', {})

    user_id = metadata.get('user_id')
    session_id = metadata.get('session_id')
    request_id = str(uuid.uuid4())  # Generate a unique request ID

    event_caller = await get_event_call(metadata)
    if event_caller is None:
        raise Exception(
            'Direct connection requires an active WebSocket session; '
            'cannot generate completion in this context (e.g. background task).'
        )

    channel = f'{user_id}:{session_id}:{request_id}'
    logging.info('WebSocket channel: %s', channel)

    if form_data.get('stream'):
        queue = asyncio.Queue()
        EVENT_QUEUES[channel] = queue

        # Start processing chat completion in background
        try:
            res = await event_caller(
                {
                    'type': 'request:chat:completion',
                    'data': {
                        'form_data': form_data,
                        'model': models[form_data['model']],
                        'channel': channel,
                        'session_id': session_id,
                    },
                }
            )

            log.info('res: %s', res)

            status = res.get('status', False)
        except BaseException:
            EVENT_QUEUES.pop(channel, None)
            raise

        if status:
            # Define a generator to stream responses
            async def event_generator():
                try:
                    while True:
                        data = await queue.get()  # Wait for new messages
                        if isinstance(data, dict):
                            if 'done' in data and data['done']:
                                break  # Stop streaming when 'done' is received

                            yield f'data: {JSONCodec.dumps(data)}\n\n'
                        elif isinstance(data, str):
                            if 'data:' in data:
                                yield f'{data}\n\n'
                            else:
                                yield f'data: {data}\n\n'
                except Exception as e:
                    log.debug('Error in event generator: %s', e)
                    pass
                finally:
                    EVENT_QUEUES.pop(channel, None)

            # Define a background task to run the event generator
            async def background():
                EVENT_QUEUES.pop(channel, None)

            # Return the streaming response
            return StreamingResponse(event_generator(), media_type='text/event-stream', background=background)
        else:
            EVENT_QUEUES.pop(channel, None)
            raise Exception(str(res))
    else:
        res = await event_caller(
            {
                'type': 'request:chat:completion',
                'data': {
                    'form_data': form_data,
                    'model': models[form_data['model']],
                    'channel': channel,
                    'session_id': session_id,
                },
            }
        )

        if 'error' in res and res['error']:
            raise Exception(res['error'])

        return res


async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
    bypass_filter: bool = False,
    bypass_system_prompt: bool = False,
):
    log.debug('generate_chat_completion: %s', form_data)
    if BYPASS_MODEL_ACCESS_CONTROL or (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL):
        bypass_filter = True

    # Propagate bypass_filter and bypass_system_prompt via request.state so that
    # downstream route handlers (openai/ollama) can read them without exposing
    # them as query parameters.
    request.state.bypass_filter = bypass_filter
    request.state.bypass_system_prompt = bypass_system_prompt

    if hasattr(request.state, 'metadata'):
        if 'metadata' not in form_data:
            form_data['metadata'] = request.state.metadata
        else:
            form_data['metadata'] = {
                **form_data['metadata'],
                **request.state.metadata,
            }

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        # Merge the direct connection model into server models so that
        # task functions (title, tags, etc.) can resolve a server-side
        # task model while still having the direct model available.
        # dict(...items()) is one HGETALL on a Redis-backed pool; ``{**pool}``
        # would issue HKEYS plus one HGET per model.
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
        log.debug('direct connection to model: %s', request.state.model['id'])
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    from open_webui.utils.oi.legacy import alias_map

    async with get_async_db() as db:
        aliases = await alias_map(db, user)
    from open_webui.utils.oi.model_targets import resolve_native_target, remap_legacy_reference

    form_data['model'] = model_id = remap_legacy_reference(model_id, aliases, models)
    registered = models.get(model_id)
    async with get_async_db() as db:
        native_target = (
            await resolve_native_target(db, model_id, user)
            if registered is None or registered.get('oi_binding') or registered.get('oi_base_binding')
            else None
        )
    if native_target is not None:
        from open_webui.utils.oi.catalog import capabilities
        from open_webui.utils.oi.model_capabilities import chat_capable

        if not chat_capable(capabilities(native_target.binding)):
            raise HTTPException(422, 'The selected model is not configured for chat')
    # Single lookup — membership check plus getitem would be two Redis
    # round trips on a Redis-backed model pool.
    model = models.get(model_id)
    if model is None and native_target is not None:
        model = {'id': model_id, 'oi_binding': True}
    if model is None:
        raise Exception('Model not found')

    if (
        native_target is None
        and getattr(request.state, 'direct', False)
        and model_id == getattr(request.state, 'model', {}).get('id')
    ):
        return await track_call(
            request,
            form_data,
            user,
            lambda _: generate_direct_chat_completion(request, form_data, user=user, models=models),
        )
    else:
        # Check if user has access to the model
        if not bypass_filter:
            try:
                await check_model_access(user, model)
            except Exception as e:
                raise e

        # Arena model — sub-model was already resolved by process_chat_payload.
        # Inject selected_model_id into the response for the frontend.
        metadata = form_data.get('metadata', {})
        selected_model_id = metadata.pop('selected_model_id', None)
        # Also clear from request.state.metadata to prevent the merge at
        # lines 177-179 from re-adding it on the recursive call.
        if hasattr(request.state, 'metadata'):
            request.state.metadata.pop('selected_model_id', None)

        # Fallback: if generate_chat_completion is called with an arena model
        # from a path that did NOT go through process_chat_payload (e.g.,
        # background tasks for title/follow-up/tags generation), resolve now.
        if not selected_model_id and model.get('owned_by') == 'arena':
            model_ids = model.get('info', {}).get('meta', {}).get('model_ids')
            filter_mode = model.get('info', {}).get('meta', {}).get('filter_mode')
            from open_webui.utils.oi.model_capabilities import accessible_arena_candidates

            selected_model_id = random.choice(
                await accessible_arena_candidates(models, user, model_ids, filter_mode, metadata)
            )

            form_data['model'] = selected_model_id

            # Gate the selected member now and preserve the caller access policy in recursion.
            if not bypass_filter:
                selected_model = request.app.state.MODELS.get(selected_model_id)
                if selected_model:
                    await check_model_access(user, selected_model)

        if selected_model_id:
            if form_data.get('stream') == True:

                async def stream_wrapper(stream):
                    yield f'data: {JSONCodec.dumps({"selected_model_id": selected_model_id})}\n\n'
                    async for chunk in stream:
                        yield chunk

                response = await generate_chat_completion(
                    request,
                    form_data,
                    user,
                    bypass_filter=bypass_filter,
                    bypass_system_prompt=bypass_system_prompt,
                )
                # Upstream errors come back as a response object.
                if not isinstance(response, StreamingResponse):
                    return response
                return StreamingResponse(
                    stream_wrapper(response.body_iterator),
                    media_type='text/event-stream',
                    background=response.background,
                )
            else:
                response = await generate_chat_completion(
                    request,
                    form_data,
                    user,
                    bypass_filter=bypass_filter,
                    bypass_system_prompt=bypass_system_prompt,
                )
                if not isinstance(response, dict):
                    return response
                return {**response, 'selected_model_id': selected_model_id}

        if native_target is not None:
            from open_webui.utils.oi.protocols import generate

            binding, connection = native_target.binding, native_target.connection
            browser_transport = None
            if connection.transport == 'browser':
                from open_webui.utils.oi.browser_transport import (
                    BrowserTransport,
                    require_browser_owner,
                    require_live_session,
                )

                await require_browser_owner(connection, user)
                browser_metadata = form_data.get('metadata') or getattr(request.state, 'metadata', {}) or {}
                require_live_session(browser_metadata, user)
                browser_transport = BrowserTransport(user, browser_metadata, binding, connection)
            if not bypass_filter:
                await check_model_access(user, models.get(model_id) or {'id': model_id, 'oi_binding': True})
                if model_id != binding.id:
                    await check_model_access(user, {'id': binding.id, 'oi_binding': True})
            form_data = copy.deepcopy(form_data)
            params = copy.deepcopy(native_target.params)
            params.pop('capability_params', None)
            system = params.pop('system', None)
            form_data = apply_model_params_to_body_openai(params, form_data)
            if not bypass_system_prompt and system:
                form_data = await apply_system_prompt_to_body(system, form_data, form_data.get('metadata'), user)
            return await track_call(
                request,
                form_data,
                user,
                lambda call_id: generate(
                    request,
                    form_data,
                    user,
                    binding,
                    connection,
                    call_id,
                    **({'transport': browser_transport} if browser_transport else {}),
                ),
                binding,
                connection,
            )
        if model.get('pipe'):
            # Below does not require bypass_filter because this is the only route the uses this function and it is already bypassing the filter
            return await track_call(
                request,
                form_data,
                user,
                lambda _: generate_function_chat_completion(request, form_data, user=user, models=models),
            )

        async def dispatch_provider(_):
            nonlocal form_data
            if model.get('owned_by') == 'ollama':
                # Apply before Ollama conversion so tool follow-ups keep the model system prompt
                if not bypass_system_prompt:
                    model_info = await Models.get_model_by_id(form_data['model'])
                    if model_info:
                        system = model_info.params.model_dump().get('system')
                        form_data = await apply_system_prompt_to_body(system, form_data, metadata, user)
                    request.state.bypass_system_prompt = True

                # Using /ollama/api/chat endpoint
                form_data = convert_payload_openai_to_ollama(form_data)
                response = await generate_ollama_chat_completion(
                    request=request,
                    form_data=form_data,
                    user=user,
                )
                if form_data.get('stream'):
                    response.headers['content-type'] = 'text/event-stream'
                    return StreamingResponse(
                        convert_streaming_response_ollama_to_openai(response),
                        headers=dict(response.headers),
                        background=response.background,
                    )
                else:
                    return convert_response_ollama_to_openai(response)
            else:
                return await generate_openai_chat_completion(
                    request=request,
                    form_data=form_data,
                    user=user,
                )

        return await track_call(request, form_data, user, dispatch_provider)


chat_completion = generate_chat_completion


async def chat_completed(request: Request, form_data: dict, user: Any):
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    data = form_data

    if not data.get('id'):
        raise Exception('Missing message id')

    model_id = data['model']
    if model_id not in models:
        raise Exception('Model not found')

    model = models[model_id]

    try:
        data = await process_pipeline_outlet_filter(request, data, user, models)
    except HTTPException:
        raise
    except Exception as e:
        raise Exception(f'Error: {e}')

    if not data.get('id'):
        raise Exception('Missing message id')

    metadata = {
        'chat_id': data['chat_id'],
        'message_id': data['id'],
        'filter_ids': data.get('filter_ids', []),
        'session_id': data['session_id'],
        'user_id': user.id,
    }

    extra_params = {
        '__event_emitter__': await get_event_emitter(metadata),
        '__event_call__': await get_event_call(metadata),
        '__user__': user.model_dump() if isinstance(user, UserModel) else {},
        '__metadata__': metadata,
        '__request__': request,
        '__model__': model,
    }

    try:
        filter_functions = await get_filter_functions(request, model, metadata.get('filter_ids', []))

        result, _ = await process_filter_functions(
            request=request,
            filter_context=None,
            filter_functions=filter_functions,
            filter_type='outlet',
            form_data=data,
            extra_params=extra_params,
        )
        return result
    except Exception as e:
        raise Exception(f'Error: {e}')
