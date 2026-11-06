"""DATA-01 boundary matrix. Prepared for one run after the implementation freezes.

No supplier traffic. Uses ASGI writes and isolated SQLite, then the real global
catalog path. Invalid input may be rejected or isolated; it may not break the
unrelated healthy binding. Only established controls get semantic assertions.
"""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from open_webui.models.customization import ModelBinding
from open_webui.utils import models
from test.oi.test_upstream_v0130_discovery_validation import client_for, create, directory

# (label, declared shape, positive-control classification)
REASONING_CASES = [
    ('unknown-null', None, 'unknown'),
    ('unknown-empty', {}, 'unknown'),
    ('unknown-no-parameter', {'levels': ['low', 'high']}, 'unknown'),
    ('valid-levels', {'parameter': 'reasoning_effort', 'levels': ['low', 'high'], 'default': 'high'}, 'levels'),
    ('valid-unset-default', {'parameter': 'reasoning_effort', 'levels': ['low', 'high']}, 'unset-default'),
    (
        'valid-budget',
        {'parameter': 'budget_tokens', 'budget': {'min': 0, 'max': 100, 'step': 10, 'default': 20}},
        'budget',
    ),
    ('reasoning-bool', True, None),
    ('reasoning-string', 'enabled', None),
    ('reasoning-list', ['high'], None),
    ('parameter-number', {'parameter': 7, 'levels': ['high']}, None),
    ('parameter-list', {'parameter': ['reasoning', 'effort'], 'levels': ['high']}, None),
    ('parameter-empty-segment', {'parameter': 'reasoning..effort', 'levels': ['high']}, None),
    ('parameter-forbidden-segment', {'parameter': '__proto__.effort', 'levels': ['high']}, None),
    ('levels-string', {'parameter': 'reasoning_effort', 'levels': 'high'}, None),
    ('levels-bool', {'parameter': 'reasoning_effort', 'levels': True}, None),
    ('levels-number', {'parameter': 'reasoning_effort', 'levels': 3}, None),
    ('levels-object', {'parameter': 'reasoning_effort', 'levels': {'high': 1}}, None),
    ('levels-unusable-elements', {'parameter': 'reasoning_effort', 'levels': [None, {}, []]}, None),
    ('budget-list', {'parameter': 'budget_tokens', 'budget': [1]}, None),
    ('budget-incomplete', {'parameter': 'budget_tokens', 'budget': {'min': 0}}, None),
]

VALID_CONTROLS = {
    'effort': {
        'label': 'Effort',
        'display': 'menu',
        'options': {'low': {'label': 'Low', 'params': {'reasoning_effort': 'low'}}},
    }
}
BUDGET_CONTROLS = {
    'budget': {
        'label': 'Budget',
        'display': 'budget',
        'budget': {'parameter': 'budget_tokens', 'min': 0, 'max': 100, 'step': 10},
    }
}
CONTROLS_CASES = [
    ('valid-empty', {}, True),
    ('valid-choice', VALID_CONTROLS, True),
    ('valid-budget', BUDGET_CONTROLS, True),
    ('null', None, False),
    ('list', ['high'], False),
    ('string', 'high', False),
    ('control-null', {'effort': None}, False),
    ('options-list', {'effort': {'label': 'Effort', 'options': ['low']}}, False),
    ('option-null', {'effort': {'label': 'Effort', 'options': {'low': None}}}, False),
]


async def two_bindings(db, monkeypatch, *, browser):
    monkeypatch.setattr(models.Config, 'get', AsyncMock(return_value=True))
    async with client_for(db) as client:
        c, b = await create(client, transport='browser' if browser else 'server')
        _, healthy = await create(client)
    return c, b, healthy


async def assert_directory_survives(db, monkeypatch, healthy_id):
    entries = await directory(db, monkeypatch)
    by_id = {entry['id']: entry for entry in entries}
    assert healthy_id in by_id, 'Unrelated healthy model disappeared'
    return by_id


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['browser-discovery', 'legacy-discovered'])
@pytest.mark.parametrize('label,reasoning,positive', REASONING_CASES, ids=[case[0] for case in REASONING_CASES])
async def test_reasoning_shape_never_breaks_other_user_catalog(db, monkeypatch, path, label, reasoning, positive):
    c, b, healthy = await two_bindings(db, monkeypatch, browser=path == 'browser-discovery')
    if path == 'browser-discovery':
        async with client_for(db) as client:
            response = await client.post(
                'connections/' + c['id'] + '/browser-models',
                json={
                    'revision': 1,
                    'models': [{'id': 'synthetic', 'capabilities': {'reasoning': deepcopy(reasoning)}}],
                },
            )
        assert response.status_code in (200, 422), response.text
        if positive:
            assert response.status_code == 200, response.text
    else:
        row = await db.get(ModelBinding, b['id'])
        row.capabilities = {'discovered': {'reasoning': deepcopy(reasoning)}, 'overrides': {}}
        await db.commit()
    entries = await assert_directory_survives(db, monkeypatch, healthy['id'])
    if positive:
        assert b['id'] in entries, 'Valid/unknown capability must not hide the binding'
        controls = entries[b['id']]['info']['params']['model_controls']
        if positive == 'unknown':
            assert 'oi_reasoning' not in controls, 'Unknown declaration must not invent a control'
        elif positive in ('levels', 'unset-default'):
            control = controls['oi_reasoning']
            assert [opt['params']['reasoning_effort'] for opt in control['options'].values()] == ['low', 'high']
            assert control.get('default') == ('level_1' if positive == 'levels' else None)
        elif positive == 'budget':
            assert controls['oi_reasoning']['budget'] == {
                'parameter': 'budget_tokens',
                'min': 0,
                'max': 100,
                'step': 10,
                'default': 20,
            }


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['binding-write', 'legacy-params'])
@pytest.mark.parametrize('label,controls,positive', CONTROLS_CASES, ids=[case[0] for case in CONTROLS_CASES])
async def test_model_control_shape_never_breaks_other_user_catalog(db, monkeypatch, path, label, controls, positive):
    c, b, healthy = await two_bindings(db, monkeypatch, browser=False)
    if path == 'binding-write':
        async with client_for(db) as client:
            response = await client.put(
                'model-bindings/' + b['id'],
                json={
                    'connection_id': c['id'],
                    'upstream_id': 'synthetic',
                    'name': 'Private',
                    'params': {'model_controls': deepcopy(controls)},
                },
            )
        assert response.status_code in (200, 422), response.text
        if positive:
            assert response.status_code == 200, response.text
    else:
        row = await db.get(ModelBinding, b['id'])
        row.params = {'model_controls': deepcopy(controls)}
        await db.commit()
    entries = await assert_directory_survives(db, monkeypatch, healthy['id'])
    if positive:
        assert b['id'] in entries
        actual = entries[b['id']]['info']['params']['model_controls']
        if label == 'valid-empty':
            assert actual == {}
        elif label == 'valid-choice':
            assert actual['effort']['options']['low']['params'] == {'reasoning_effort': 'low'}
        elif label == 'valid-budget':
            assert actual['budget']['budget']['max'] == 100
            assert actual['budget']['budget']['step'] == 10
