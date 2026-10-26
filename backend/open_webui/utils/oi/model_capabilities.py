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
