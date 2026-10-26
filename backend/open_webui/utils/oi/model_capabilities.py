"""Shared chat eligibility; unknown legacy capabilities never imply a new capability."""

SPECIALIZED = ('embedding', 'rerank', 'tts', 'stt', 'realtime', 'image_generation', 'image_edit')


def chat_capable(capabilities):
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    if isinstance(capabilities.get('chat'), bool):
        return capabilities['chat']
    return not any(capabilities.get(key) is True for key in SPECIALIZED)


def chat_model(model):
    profile = ((model.get('info') or {}).get('meta') or {}).get('capabilities') or {}
    return profile.get('chat') is not False and chat_capable(model.get('capabilities'))


def arena_candidates(models, members=None, filter_mode=None):
    from fastapi import HTTPException

    candidates = [model['id'] for model in models.values() if model.get('owned_by') != 'arena' and chat_model(model)]
    if isinstance(members, list) and members:
        candidates = [key for key in candidates if (key not in members if filter_mode == 'exclude' else key in members)]
    if not candidates:
        raise HTTPException(422, 'No configured chat models are available for this Arena')
    return candidates


async def accessible_arena_candidates(models, user, members=None, filter_mode=None, metadata=None):
    """Resolve eligibility before any selected model's parameters or knowledge are read."""
    from fastapi import HTTPException
    from open_webui.utils.models import get_filtered_models
    from open_webui.utils.oi.browser_transport import require_live_session

    visible = await get_filtered_models(list(models.values()), user)
    allow_browser = False
    try:
        require_live_session(metadata or {}, user)
        allow_browser = True
    except HTTPException:
        pass
    eligible = {model['id']: model for model in visible if allow_browser or not model.get('direct')}
    return arena_candidates(eligible, members, filter_mode)
