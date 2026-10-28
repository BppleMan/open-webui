"""Run isolated PostgreSQL cost and archive acceptance. No external provider calls.

Requires an empty loopback oi_stage7_* PostgreSQL database owned by oi_test.
Ports 8112/8113 must be free. Uses a fresh PostgreSQL app DB and synthetic users.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

repo = Path(__file__).resolve().parents[2]
os.chdir(repo)
api_port = int(os.environ.get('OI_STAGE7_API_PORT', '8112'))
provider_port = int(os.environ.get('OI_STAGE7_PROVIDER_PORT', '8113'))
for port in [api_port, provider_port]:
    with socket.socket() as guard:
        guard.bind(('127.0.0.1', port))
state = Path(tempfile.mkdtemp(prefix='oi-stage7-'))
url = os.environ['OI_STAGE7_DATABASE_URL']
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, inspect

target = make_url(url)
assert (
    target.host in {'127.0.0.1', 'localhost'}
    and target.username == 'oi_test'
    and target.database.startswith('oi_stage7_')
)
engine = create_engine(url)
assert not inspect(engine).get_table_names(), 'Use a fresh isolated database'
engine.dispose()
print(f'Stage7 state: {state}', flush=True)
env = {
    **os.environ,
    'OI_STAGE7_STATE': str(state),
    'OI_STAGE7_API': f'http://127.0.0.1:{api_port}',
    'OI_STAGE7_PROVIDER': f'http://127.0.0.1:{provider_port}',
    'DATA_DIR': str(state / 'data'),
    'DATABASE_URL': url,
    'PGVECTOR_DB_URL': url,
    'VECTOR_DB': 'pgvector',
    'PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH': '3',
    'PGVECTOR_USE_HALFVEC': 'false',
    'USE_SLIM_DOCKER': 'true',
    'ENABLE_DB_MIGRATIONS': 'true',
    'WEBUI_SECRET_KEY': 'oi-stage7-synthetic',
    'ENABLE_SIGNUP': 'true',
    'ENABLE_OPENAI_API': 'false',
    'ENABLE_OLLAMA_API': 'false',
    'PYTHONPATH': 'backend',
}
(state / 'data').mkdir()
processes = []
try:
    for name, module, port, extra in [
        ('provider', 'stage7_provider:app', provider_port, ['--app-dir', 'scripts/oi/fixtures']),
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
    subprocess.run(
        [sys.executable, 'scripts/oi/stage7-http-acceptance.py'],
        env={**env, 'ENABLE_DB_MIGRATIONS': 'false'},
        check=True,
        timeout=240,
    )
    (repo / '.oi-local').mkdir(exist_ok=True)
    (repo / '.oi-local/stage7-state.txt').write_text(str(state))
    print(f'Stage7 PostgreSQL billing/archive scenarios passed. Evidence: {state}')

finally:
    for process in reversed(processes):
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
