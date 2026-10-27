"""Run isolated native protocol and context acceptance. No external provider calls.

Uses pgvector when PGVECTOR_DB_URL is supplied, otherwise local Chroma.
Ports 8110/8111 must be free. Uses a fresh temporary SQLite app DB and synthetic users.
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
api_port = int(os.environ.get('OI_STAGE6_API_PORT', '8110'))
provider_port = int(os.environ.get('OI_STAGE6_PROVIDER_PORT', '8111'))
for port in [api_port, provider_port]:
    with socket.socket() as guard:
        guard.bind(('127.0.0.1', port))
state = Path(tempfile.mkdtemp(prefix='oi-stage6-'))
env = {
    **os.environ,
    'OI_STAGE6_STATE': str(state),
    'OI_STAGE6_API': f'http://127.0.0.1:{api_port}',
    'OI_STAGE6_PROVIDER': f'http://127.0.0.1:{provider_port}',
    'DATA_DIR': str(state / 'data'),
    'DATABASE_URL': f'sqlite:///{state}/data/webui.db',
    'VECTOR_DB': 'pgvector' if os.environ.get('PGVECTOR_DB_URL') else 'chroma',
    'PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH': '3',
    'PGVECTOR_USE_HALFVEC': 'false',
    'USE_SLIM_DOCKER': 'true',
    'ENABLE_DB_MIGRATIONS': 'true',
    'WEBUI_SECRET_KEY': 'oi-stage6-synthetic',
    'ENABLE_SIGNUP': 'true',
    'ENABLE_OPENAI_API': 'false',
    'ENABLE_OLLAMA_API': 'false',
    'PYTHONPATH': 'backend',
}
(state / 'data').mkdir()
processes = []
try:
    for name, module, port, extra in [
        ('provider', 'stage6_provider:app', provider_port, ['--app-dir', 'scripts/oi/fixtures']),
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
    subprocess.run([sys.executable, 'scripts/oi/stage6-http-acceptance.py'], env=env, check=True, timeout=180)
    print(f'Stage6 native HTTP/control/compaction scenarios passed. Evidence: {state}')

finally:
    for process in reversed(processes):
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
