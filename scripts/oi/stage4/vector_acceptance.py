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
