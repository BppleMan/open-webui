"""All three branch entry points use the real Alembic graph in isolated processes."""

import os, subprocess, sys, sqlite3
import pytest


@pytest.mark.parametrize('seed', ['empty', 'oi7', 'upstream'])
def test_migration_merge_and_restore(tmp_path, seed):
    database = tmp_path / 'upgrade.sqlite'
    env = {
        **os.environ,
        'DATABASE_URL': f'sqlite:///{database}',
        'DATA_DIR': str(tmp_path / 'data'),
        'ENABLE_DB_MIGRATIONS': 'false',
        'USE_SLIM_DOCKER': 'true',
        'WEBUI_SECRET_KEY': 'synthetic-migration-key',
        'PYTHONPATH': 'backend',
    }
    run = subprocess.run(
        [sys.executable, 'scripts/oi/upstream-v0130-migration.py', seed], env=env, text=True, capture_output=True
    )
    assert run.returncode == 0, run.stdout + run.stderr
    copy = tmp_path / 'restored.sqlite'
    with sqlite3.connect(database) as source, sqlite3.connect(copy) as restored:
        source.backup(restored)
        assert restored.execute('select version_num from alembic_version').fetchone()[0] == 'oi0000000008'
        assert (
            restored.execute('select count(*) from sqlite_master where type="table"').fetchone()
            == source.execute('select count(*) from sqlite_master where type="table"').fetchone()
        )
