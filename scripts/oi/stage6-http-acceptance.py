"""Real isolated OI HTTP -> synthetic provider protocol and reasoning matrix."""

import os, json, pathlib, uuid, httpx

p = pathlib.Path(os.environ['OI_STAGE6_STATE'])
p.mkdir(exist_ok=True, parents=True)
api = os.environ.get('OI_STAGE6_API', 'http://127.0.0.1:8108')
provider = os.environ.get('OI_STAGE6_PROVIDER', 'http://127.0.0.1:8109')
with httpx.Client(base_url=api, timeout=90, trust_env=False) as c:

    def req(method, path, body=None):
        r = c.request(method, path, json=body)
        r.raise_for_status()
        return r.json()

    r = c.post(
        '/api/v1/auths/signup',
        json={
            'name': 'Stage6 Owner',
            'email': 'oi-stage6-owner@example.invalid',
            'password': 'stage6-synthetic-password',
        },
    )
    if r.status_code != 200:
        r = c.post(
            '/api/v1/auths/signin',
            json={'email': 'oi-stage6-owner@example.invalid', 'password': 'stage6-synthetic-password'},
        )
    r.raise_for_status()
    owner = r.json()
    c.headers['Authorization'] = 'Bearer ' + owner['token']
    req(
        'POST',
        '/api/v1/configs/import',
        {'config': {'task.title.enable': False, 'task.tags.enable': False, 'task.follow_up.enable': False}},
    )
    report = []
    bindings = {}
    for protocol, template, parameter in [
        ('openai-chat', 'openai-compatible', 'reasoning_effort'),
        ('openai-responses', 'openai', 'reasoning.effort'),
        ('anthropic', 'anthropic', 'output_config.effort'),
        ('gemini', 'gemini', 'generationConfig.thinkingConfig.thinkingLevel'),
        ('gemini-interactions', 'gemini', 'generation_config.thinking_level'),
        ('cpa-agy', 'cpa-agy', 'generationConfig.thinkingConfig.thinkingLevel'),
        ('ollama', 'ollama', 'think'),
    ]:
        con = req(
            'POST',
            '/api/v1/connections',
            {
                'name': 'Stage6 ' + protocol,
                'template': template,
                'protocol': protocol,
                'base_url': provider + '/' + protocol + '/v1',
                'settings': {'auth': 'none'},
                'shared': True,
            },
        )
        levels = [False, True] if protocol == 'ollama' else ['low', 'high']
        model = req(
            'POST',
            '/api/v1/model-bindings',
            {
                'connection_id': con['id'],
                'upstream_id': 'fixture',
                'name': 'Stage6 ' + protocol,
                'protocol': protocol,
                'capabilities': {
                    'chat': True,
                    'vision': True,
                    'input_limit': 10000,
                    'context_window': 12000,
                    'output_limit': 1024,
                    'reasoning': {'parameter': parameter, 'levels': levels},
                },
            },
        )
        bindings[protocol] = model
        for stream in [False, True]:
            chat_id, message_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
            req('POST', '/api/v1/chats/drafts', {'chat_id': chat_id})
            text = 'Synthetic native protocol acceptance.'
            form = {
                'model': model['id'],
                'chat_id': chat_id,
                'id': message_id,
                'parent_id': None,
                'oi_request_id': str(uuid.uuid4()),
                'stream': stream,
                'messages': [{'role': 'user', 'content': text}],
                'user_message': {
                    'id': user_id,
                    'parentId': None,
                    'role': 'user',
                    'content': text,
                    'models': [model['id']],
                },
                'params': {'max_tokens': 128, 'model_controls': {model['id']: {'oi_reasoning': 'level_1'}}},
            }
            r = c.post('/api/chat/completions', json=form)
            r.raise_for_status()
            observed = json.dumps(req('GET', f'/api/v1/chats/{chat_id}')) if stream else r.text
            assert 'Native stage6 fixture.' in observed, (protocol, stream, observed[:300])
            if protocol in {'gemini', 'cpa-agy'}:
                assert 'Synthetic streamed thought summary.' in observed, (protocol, stream, observed[:300])
            usage = req('GET', f'/api/v1/chats/{chat_id}/usage')
            assert len(usage['calls']) == 1
            call = usage['calls'][0]
            assert call['usage']['input_tokens'] == 100, (protocol, call['usage'])
            event = httpx.get(provider + '/events').json()[-1]
            value = event['body']
            for key in parameter.split('.'):
                value = value[key]
            assert value == levels[1], (protocol, 'reasoning parameter mismatch', value)
            assert 'metadata' not in event['body']
            if protocol == 'cpa-agy':
                assert all(event['rp'].values())
            report.append(
                {
                    'protocol': protocol,
                    'stream': stream,
                    'chat_id': chat_id,
                    'call_id': call['id'],
                    'usage': call['usage'],
                    'outbound_reasoning': value,
                    'status': call['status'],
                }
            )
        print(protocol, 'stream/nonstream/reasoning/ledger PASS', flush=True)
    # Explicit budget choices travel through the same native model controls.
    gemini = bindings['gemini']
    budget_model = req(
        'POST',
        '/api/v1/model-bindings',
        {
            'connection_id': gemini['connection_id'],
            'upstream_id': 'fixture-budget',
            'name': 'Stage6 budget',
            'protocol': 'gemini',
            'capabilities': {
                'chat': True,
                'reasoning': {
                    'parameter': 'generationConfig.thinkingConfig.thinkingBudget',
                    'budget': {'min': 1024, 'max': 16384, 'step': 1024},
                },
            },
        },
    )
    bindings['budget'] = budget_model

    def send(model, parent=None, chat=None, text='Synthetic follow-up.', control=None):
        chat = chat or str(uuid.uuid4())
        if parent is None:
            req('POST', '/api/v1/chats/drafts', {'chat_id': chat})
        uid, aid = str(uuid.uuid4()), str(uuid.uuid4())
        payload = {
            'model': model['id'],
            'chat_id': chat,
            'id': aid,
            'parent_id': parent,
            'oi_request_id': str(uuid.uuid4()),
            'stream': True,
            'messages': [{'role': 'user', 'content': text}],
            'user_message': {'id': uid, 'parentId': parent, 'role': 'user', 'content': text, 'models': [model['id']]},
            'params': {'max_tokens': 128},
        }
        if control is not None:
            payload['params']['model_controls'] = {model['id']: {'oi_reasoning': control}}
        response = c.post('/api/chat/completions', json=payload)
        response.raise_for_status()
        return chat, aid

    budget_chat, _ = send(budget_model, control=8192)
    captured = httpx.get(provider + '/events').json()[-1]['body']
    assert captured['generationConfig']['thinkingConfig']['thinkingBudget'] == 8192
    # Default leaves reasoning to the provider instead of silently picking the first level.
    chat, aid = send(gemini, text='KEEP_ORIGINAL_STAGE6 ' * 100)
    captured = httpx.get(provider + '/events').json()[-1]['body']
    assert captured['generationConfig']['thinkingConfig'] == {'includeThoughts': True}
    before = req('GET', f'/api/v1/chats/{chat}')['chat']['history']['messages']
    before_events = len(httpx.get(provider + '/events').json())
    assert not req('POST', f'/api/v1/chats/{chat}/compact', {'model': gemini['id']})['compacted']
    assert len(httpx.get(provider + '/events').json()) == before_events
    req(
        'POST',
        '/api/v1/configs/import',
        {'config': {'chat.context_compaction.enable': True, 'chat.context_compaction.token_threshold': 1}},
    )
    result = req('POST', f'/api/v1/chats/{chat}/compact', {'model': gemini['id']})
    assert result['compacted'], result
    after = req('GET', f'/api/v1/chats/{chat}')['chat']['history']['messages']
    for mid, message in before.items():
        assert after[mid]['content'] == message['content']
        assert after[mid].get('oi_native') == message.get('oi_native')
    assert after[aid]['contextSummary'] == 'Native stage6 fixture.'
    calls = req('GET', f'/api/v1/chats/{chat}/usage')['calls']
    assert len(calls) == 2, calls
    assert any(call['usage'].get('context', {}).get('purpose') == 'context_compaction' for call in calls)
    before_events = len(httpx.get(provider + '/events').json())
    _, aid = send(gemini, parent=aid, chat=chat)
    events = httpx.get(provider + '/events').json()
    assert len(events) == before_events + 1, 'Manual compaction must not enable automatic summarization'
    captured = events[-1]['body']
    assert 'KEEP_ORIGINAL_STAGE6' not in json.dumps(captured)
    assert 'Native stage6 fixture.' in json.dumps(captured)
    req('POST', '/api/v1/configs/import', {'config': {'chat.context_compaction.enable': False}})
    send(gemini, parent=aid, chat=chat)
    assert 'KEEP_ORIGINAL_STAGE6' in json.dumps(httpx.get(provider + '/events').json()[-1]['body'])
    (p / 'context-evidence.json').write_text(
        json.dumps(
            {
                'budget_wire': 8192,
                'unknown_reasoning_default_not_injected': True,
                'default_compaction_disabled': True,
                'manual_summary_call_count': 1,
                'original_history_and_native_preserved': True,
                'summary_attributed_to_chat': True,
                'checkpoint_applied_only_when_enabled': True,
                'no_automatic_summary_calls': True,
                'chat_id': chat,
                'budget_chat': budget_chat,
            },
            indent=2,
        )
    )
    print('Budget/default reasoning/explicit compaction/original history/ledger PASS', flush=True)
    req('POST', '/api/v1/configs/import', {'config': {'ui.default_models': bindings['gemini']['id']}})
    (p / 'seed.json').write_text(json.dumps({'owner': owner, 'bindings': bindings}))
    (p / 'seed.json').chmod(0o600)
    (p / 'http-evidence.json').write_text(json.dumps(report, indent=2))
    print('Stage6 HTTP matrix passed', flush=True)
