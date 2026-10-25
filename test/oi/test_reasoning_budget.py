from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.models.models import ModelControl
from open_webui.utils.models import public_model_params
from open_webui.utils.oi.catalog import reasoning_controls
from open_webui.utils.payload import apply_model_controls
from pydantic import ValidationError


def controls(default=4096):
    return reasoning_controls(
        SimpleNamespace(
            capabilities={
                'reasoning': {
                    'parameter': 'generationConfig.thinkingConfig.thinkingBudget',
                    'budget': {'min': 1024, 'max': 16384, 'step': 1024, 'default': default},
                }
            }
        )
    )


def test_budget_is_native_control_with_exact_nested_numeric_payload():
    raw = controls()
    model = ModelControl(**raw['oi_reasoning'])
    assert model.display == 'budget'
    public = public_model_params({'model_controls': raw})
    assert 'parameter' not in public['model_controls']['oi_reasoning']['budget']
    params = apply_model_controls({}, raw, {'oi_reasoning': '8192'})
    assert params == {'custom_params': {'generationConfig': {'thinkingConfig': {'thinkingBudget': 8192}}}}
    assert apply_model_controls({}, controls(None), {}) == {}
    assert (
        apply_model_controls({}, raw, {})['custom_params']['generationConfig']['thinkingConfig']['thinkingBudget']
        == 4096
    )


@pytest.mark.parametrize('value', ['0', '20000', '1500', 'NaN', 'Infinity', '1.5', True])
def test_unsupported_budget_values_cannot_be_sent(value):
    with pytest.raises(HTTPException) as error:
        apply_model_controls({}, controls(), {'oi_reasoning': value})
    assert error.value.status_code == 400


def test_invalid_capability_range_is_rejected():
    raw = controls()['oi_reasoning']
    raw['budget']['step'] = 0
    with pytest.raises(ValidationError):
        ModelControl(**raw)
    raw['budget'].update(step=1024, parameter='constructor.value')
    with pytest.raises(ValidationError):
        ModelControl(**raw)
