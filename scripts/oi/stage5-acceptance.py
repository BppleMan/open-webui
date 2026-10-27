"""Run isolated session, draft, PostgreSQL and Redis acceptance. No external provider calls.

Requires a fresh disposable loopback PostgreSQL database (oi_test / oi_stage5_*), with pgvector and Redis.
Ports 8106/8107 must be free. Uses a fresh PostgreSQL app DB and synthetic users.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, inspect
from urllib.parse import urlsplit

repo = Path(__file__).resolve().parents[2]
os.chdir(repo)
url = os.environ['OI_STAGE5_DATABASE_URL']
target = make_url(url)
assert (
    target.host in {'127.0.0.1', 'localhost'}
    and target.username == 'oi_test'
    and target.database.startswith('oi_stage5_')
)
assert urlsplit(os.environ['OI_STAGE5_REDIS_URL']).hostname in {'127.0.0.1', 'localhost'}
engine = create_engine(url)
assert not inspect(engine).get_table_names(), (
    'Use a fresh stage5 database; existing data is never modified by this runner'
)
engine.dispose()
api_port = int(os.environ.get('OI_STAGE5_API_PORT', '8106'))
provider_port = int(os.environ.get('OI_STAGE5_PROVIDER_PORT', '8107'))
for port in [api_port, provider_port]:
    with socket.socket() as guard:
        guard.bind(('127.0.0.1', port))
state = Path(tempfile.mkdtemp(prefix='oi-stage5-'))
env = {
    **os.environ,
    'OI_STAGE5_STATE': str(state),
    'OI_STAGE5_API': f'http://127.0.0.1:{api_port}',
    'OI_STAGE5_PROVIDER': f'http://127.0.0.1:{provider_port}',
    'DATA_DIR': str(state / 'data'),
    'DATABASE_URL': url,
    'PGVECTOR_DB_URL': url,
    'REDIS_URL': os.environ['OI_STAGE5_REDIS_URL'],
    'REDIS_KEY_PREFIX': 'oi-stage5-' + state.name,
    'VECTOR_DB': 'pgvector',
    'PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH': '3',
    'PGVECTOR_USE_HALFVEC': 'false',
    'USE_SLIM_DOCKER': 'true',
    'ENABLE_DB_MIGRATIONS': 'true',
    'WEBUI_SECRET_KEY': 'oi-stage5-synthetic',
    'ENABLE_SIGNUP': 'true',
    'ENABLE_OPENAI_API': 'false',
    'ENABLE_OLLAMA_API': 'false',
    'PYTHONPATH': 'backend',
}
(state / 'data').mkdir()
processes = []
try:
    for name, module, port, extra in [
        ('provider', 'stage4_provider:app', provider_port, ['--app-dir', 'scripts/oi/fixtures']),
        ('backend', 'open_webui.main:app', api_port, []),
    ]:
        log = (state / f'{name}.log').open('w')
        process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', module, '--host', '127.0.0.1', '--port', str(port), *extra],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError(f'{name} exited; see {state}')
            try:
                with urlopen(
                    f'http://127.0.0.1:{port}/' + ('events' if name == 'provider' else 'health'), timeout=2
                ) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError(f'{name} startup timed out')
    subprocess.run([sys.executable, 'scripts/oi/stage5-http-acceptance.py'], env=env, check=True, timeout=180)
    print(f'Stage5 authenticated HTTP/WS/PostgreSQL/Redis scenarios passed. Evidence: {state}')

finally:
    for process in reversed(processes):
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
