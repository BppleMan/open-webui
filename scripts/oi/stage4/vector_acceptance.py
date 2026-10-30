"""Synthetic stage4 integration scenario; run with scripts/oi/stage4-acceptance.py."""

import os
import httpx, json, pathlib, psycopg, uuid

p = pathlib.Path(os.environ['OI_STAGE4_STATE'])
seed = json.loads((p / 'seed.json').read_text())
c = httpx.Client(
    base_url=os.environ.get('OI_STAGE4_API', 'http://127.0.0.1:8094'),
    headers={'Authorization': 'Bearer ' + seed['owner']['token']},
    timeout=90,
)


def call(method, path, **kw):
    r = c.request(method, path, **kw)
    assert r.is_success, (path, r.status_code, r.text[:250])
    return r.json()


a = seed['bindings']['a_cap']['id']
b = seed['bindings']['b_cap']['id']
call(
    'POST',
    '/api/v1/retrieval/embedding/update',
    json={'RAG_EMBEDDING_ENGINE': 'connection', 'RAG_EMBEDDING_MODEL': a, 'RAG_EMBEDDING_BATCH_SIZE': 4},
)
call(
    'POST',
    '/api/v1/retrieval/config/update',
    json={
        'RAG_RERANKING_ENGINE': 'connection',
        'RAG_RERANKING_MODEL': b,
        'ENABLE_RAG_HYBRID_SEARCH': True,
        'TOP_K': 3,
        'TOP_K_RERANKER': 2,
        'CHUNK_SIZE': 150,
        'CHUNK_OVERLAP': 20,
        'HYBRID_BM25_WEIGHT': 0.2,
    },
)
files = []
for label in ['alpha', 'beta']:
    f = call(
        'POST',
        '/api/v1/files/',
        params={'process': 'false'},
        files={
            'file': (
                label + '.txt',
                f'Synthetic {label} record. The {label} key is {label.upper()}-STAGE4.\n'.encode(),
                'text/plain',
            )
        },
    )
    result = call('POST', '/api/v1/retrieval/process/file', json={'file_id': f['id']})
    files.append({'file_id': f['id'], 'collection_name': result.get('collection_name') or 'file-' + f['id']})
result = call(
    'POST',
    '/api/v1/retrieval/query/collection',
    json={
        'collection_names': [f['collection_name'] for f in files],
        'query': 'alpha key',
        'k': 2,
        'k_reranker': 1,
        'hybrid': True,
    },
)
assert 'ALPHA-STAGE4' in str(result), result
with psycopg.connect(os.environ['PGVECTOR_DB_URL']) as db:
    rows = db.execute(
        'select collection_name, count(*), count(vector) from document_chunk group by collection_name'
    ).fetchall()
    assert all(any(row[0] == f['collection_name'] and row[1] > 0 and row[2] == row[1] for row in rows) for f in files)
events = httpx.get(os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095') + '/events').json()
assert any(e['connection'] == 'a' and e['operation'] == 'embeddings' for e in events)
assert any(e['connection'] == 'b' and e['operation'] == 'rerank' for e in events)
report = {'files': files, 'persistent_vectors': rows, 'result': result, 'provider_events': events}
(p / 'vector-evidence.json').write_text(json.dumps(report, indent=2))
print('Real upload, pgvector indexed readback and A-embedding/B-rerank query passed')

# Full chat boundary: a retrieval failure must stop before the main model call.
provider = os.environ.get('OI_STAGE4_PROVIDER', 'http://127.0.0.1:8095')
call('POST', '/api/v1/configs/import', json={'config': {'task.query.retrieval.enable': False}})
for operation in ['embeddings', 'rerank']:
    httpx.post(provider + '/mode', json={'mode': operation + '_limited'}).raise_for_status()
    before = len(httpx.get(provider + '/events').json())
    try:
        chat, mid, uid = [str(uuid.uuid4()) for _ in range(3)]
        call('POST', '/api/v1/chats/drafts', json={'chat_id': chat})
        response = c.post(
            '/api/chat/completions',
            json={
                'model': seed['bindings']['a_chat']['id'],
                'chat_id': chat,
                'id': mid,
                'oi_request_id': str(uuid.uuid4()),
                'stream': False,
                'messages': [{'role': 'user', 'content': 'alpha key'}],
                'user_message': {'id': uid, 'role': 'user', 'content': 'alpha key'},
                'files': [{'id': files[0]['file_id'], 'type': 'file', 'collection_name': files[0]['collection_name']}],
                'params': {'function_calling': 'legacy'},
                'background_tasks': {},
            },
        )
        assert response.status_code == 429, (response.status_code, response.text[:300])
        assert response.headers.get('retry-after') == '7'
        recent = httpx.get(provider + '/events').json()[before:]
        assert any(e['operation'] == operation for e in recent), recent
        assert not any(e['operation'] == 'chat/completions' for e in recent), recent
    finally:
        httpx.post(provider + '/mode', json={'mode': 'normal'}).raise_for_status()
print('Full chat embedding/rerank 429 preserves Retry-After and makes no main model request')
