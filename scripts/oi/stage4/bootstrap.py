"""Synthetic stage4 integration scenario; run with scripts/oi/stage4-acceptance.py."""

import os
import httpx, json, pathlib

p = pathlib.Path(os.environ['OI_STAGE4_STATE'])
c = httpx.Client(base_url=os.environ.get('OI_STAGE4_API', 'http://127.0.0.1:8094'), timeout=45)
r = c.post(
    '/api/v1/auths/signup',
    json={'name': 'Stage4 Owner', 'email': 'oi-stage4-owner@example.invalid', 'password': 'stage4-synthetic-password'},
)
if r.status_code != 200:
    r = c.post(
        '/api/v1/auths/signin',
        json={'email': 'oi-stage4-owner@example.invalid', 'password': 'stage4-synthetic-password'},
    )
r.raise_for_status()
owner = r.json()
c.headers['Authorization'] = 'Bearer ' + owner['token']
(p / 'openapi.json').write_text(json.dumps(c.get('/openapi.json').json()))
connections = {}
bindings = {}
for key in ['a', 'b']:
    r = c.post(
        '/api/v1/connections',
        json={
            'name': 'Stage4 ' + key.upper(),
            'template': 'openai-compatible',
            'protocol': 'openai-chat',
            'transport': 'server',
            'base_url': os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095') + f'/{key}/v1',
            'api_key': 'synthetic-' + key,
            'settings': {'cost_currency': 'USD'},
            'shared': True,
            'enabled': True,
        },
    )
    r.raise_for_status()
    connections[key] = r.json()
    for kind, cap in [
        (
            'chat',
            {'chat': True, 'tools': True, 'reasoning': {'parameter': 'reasoning_effort', 'levels': ['low', 'high']}},
        ),
        (
            'cap',
            {
                'embedding': True,
                'rerank': True,
                'tts': True,
                'stt': True,
                'realtime': True,
                'image_generation': True,
                'image_edit': True,
            },
        ),
    ]:
        r = c.post(
            '/api/v1/model-bindings',
            json={
                'connection_id': connections[key]['id'],
                'upstream_id': 'same-model' if kind == 'chat' else 'cap-model',
                'name': 'Same ' + kind,
                'protocol': 'openai-chat',
                'capabilities': cap,
                'enabled': True,
                'params': {},
                'pricing': {},
            },
        )
        r.raise_for_status()
        bindings[key + '_' + kind] = r.json()
seed = {'owner': owner, 'connections': connections, 'bindings': bindings}
(p / 'seed.json').write_text(json.dumps(seed, indent=2))
(p / 'seed.json').chmod(0o600)
print('Independent synthetic owner and A/B chat/capability bindings created')
