"""Run isolated HTTP, WebSocket and real pgvector acceptance. No external provider calls.

Requires a disposable loopback PostgreSQL database (oi_test / oi_*), with pgvector.
Ports 8094/8095 must be free. Uses a fresh temporary SQLite app DB and synthetic users.
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

repo = Path(__file__).resolve().parents[2]
os.chdir(repo)
url = os.environ['PGVECTOR_DB_URL']
target = make_url(url)
assert target.host in {'127.0.0.1', 'localhost'} and target.username == 'oi_test' and target.database.startswith('oi_')
api_port = int(os.environ.get('OI_STAGE4_API_PORT', '8094'))
provider_port = int(os.environ.get('OI_STAGE4_PROVIDER_PORT', '8095'))
for port in [api_port, provider_port]:
    with socket.socket() as guard:
        guard.bind(('127.0.0.1', port))
state = Path(tempfile.mkdtemp(prefix='oi-stage4-'))
env = {
    **os.environ,
    'OI_STAGE4_STATE': str(state),
    'OI_STAGE4_API': f'http://127.0.0.1:{api_port}',
    'OI_STAGE4_PROVIDER': f'http://127.0.0.1:{provider_port}',
    'DATA_DIR': str(state / 'data'),
    'DATABASE_URL': f'sqlite:///{state}/data/webui.db',
    'VECTOR_DB': 'pgvector',
    'PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH': '3',
    'PGVECTOR_USE_HALFVEC': 'false',
    'USE_SLIM_DOCKER': 'true',
    'ENABLE_DB_MIGRATIONS': 'true',
    'WEBUI_SECRET_KEY': 'oi-stage4-synthetic',
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
    for scenario in [
        'bootstrap',
        'surfaces',
        'surfaces_more',
        'vector_acceptance',
        'subagent_acceptance',
        'realtime_acceptance',
        'remaining',
    ]:
        subprocess.run([sys.executable, f'scripts/oi/stage4/{scenario}.py'], env=env, check=True, timeout=180)
    print(f'Stage4 HTTP/WebSocket/pgvector scenarios passed. Synthetic evidence: {state}')
finally:
    for process in reversed(processes):
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
