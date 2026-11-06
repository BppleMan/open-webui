"""Validate native model configuration without letting old private data break catalogs."""

from copy import deepcopy
from open_webui.models.models import ModelParams
from pydantic import ValidationError


def validate_native_params(params):
    ModelParams.model_validate(params)
    if params.get('provider_options') is not None and not isinstance(params['provider_options'], dict):
        raise ValueError('provider_options must be an object')
    if params.get('capability_params') is not None and (
        not isinstance(params['capability_params'], dict)
        or any(not isinstance(value, dict) for value in params['capability_params'].values())
    ):
        raise ValueError('capability_params and each capability configuration must be objects')
    return params


def safe_native_params(params):
    params = deepcopy(params) if isinstance(params, dict) else {}
    try:
        ModelParams.model_validate({'model_controls': params.get('model_controls', {})})
    except (ValidationError, ValueError, TypeError):
        params['model_controls'] = {}
    for control in params.get('model_controls', {}).values():
        control.setdefault('options', {})
    if 'provider_options' in params and not isinstance(params['provider_options'], dict):
        params['provider_options'] = {}
    capability_params = params.get('capability_params')
    if capability_params is not None:
        params['capability_params'] = (
            {key: value for key, value in capability_params.items() if isinstance(value, dict)}
            if isinstance(capability_params, dict)
            else {}
        )
    return params
