"""Compare synthetic PostgreSQL stage7 content after pg_dump/pg_restore."""

import hashlib, json, os, sys
from pathlib import Path
import sqlalchemy as sa

url = os.environ['OI_STAGE7_DATABASE_URL']
target = sa.engine.make_url(url)
assert (
    target.host in {'127.0.0.1', 'localhost'}
    and target.username == 'oi_test'
    and target.database.startswith('oi_stage7_')
)
engine = sa.create_engine(url)
result = {}
with engine.connect() as db:
    tables = [
        t
        for t in sa.inspect(db).get_table_names()
        if t.startswith('oi_') or t in {'chat', 'chat_message', 'file', 'user', 'config', 'alembic_version'}
    ]
    for name in sorted(tables):
        table = sa.Table(name, sa.MetaData(), autoload_with=db)
        rows = [dict(row) for row in db.execute(sa.select(table)).mappings()]
        encoded = json.dumps(
            sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str)), sort_keys=True, default=str
        ).encode()
        result[name] = {'rows': len(rows), 'sha256': hashlib.sha256(encoded).hexdigest()}
    assert db.execute(sa.text('select version_num from alembic_version')).scalar_one() == 'oi0000000006'
path = Path(os.environ.get('OI_STAGE7_MANIFEST', '.oi-local/stage7-db-manifest.json'))
if sys.argv[1] == 'prepare':
    path.write_text(json.dumps(result, indent=2) + '\n')
else:
    assert result == json.loads(path.read_text()), 'Restored tables differ from stage7 acceptance database'
print(f'{len(result)} table content hashes verified: {sys.argv[1]}')
