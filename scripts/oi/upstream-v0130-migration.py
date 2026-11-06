"""Isolated migration merge acceptance. Requires an empty SQLite file or loopback oi_* PostgreSQL DB."""

import json, os, sys
from pathlib import Path
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

url = os.environ['DATABASE_URL']
target = sa.engine.make_url(url)
assert target.drivername.startswith('sqlite') or (
    target.host in {'127.0.0.1', 'localhost'} and target.database.startswith('oi_') and target.username == 'oi_test'
)
seed = sys.argv[1]
assert seed in {'empty', 'oi7', 'upstream'}
config = Config('backend/open_webui/alembic.ini')
config.set_main_option('script_location', 'backend/open_webui/migrations')
assert ScriptDirectory.from_config(config).get_heads() == ['oi0000000008']
engine = sa.create_engine(url)
assert not sa.inspect(engine).get_table_names(), 'Requires an empty isolated database'
if seed != 'empty':
    command.upgrade(config, 'oi0000000007' if seed == 'oi7' else 'e42c8f710a36')
old = {}
if seed != 'empty':
    with engine.begin() as db:
        chat = sa.Table('chat', sa.MetaData(), autoload_with=db)
        db.execute(
            chat.insert().values(
                id='upgrade-chat',
                user_id='upgrade-owner',
                title='preserved',
                chat={
                    'history': {
                        'currentId': 'm',
                        'messages': {'m': {'id': 'm', 'role': 'assistant', 'content': 'preserved transcript'}},
                    }
                },
                created_at=1,
                updated_at=1,
            )
        )
        tool = sa.Table('tool', sa.MetaData(), autoload_with=db)
        db.execute(
            tool.insert().values(
                id='upgrade-tool',
                user_id='upgrade-owner',
                name='preserved',
                content='class Tools: pass',
                specs='[]',
                meta='{}',
                valves='{}',
                created_at=1,
                updated_at=1,
            )
        )
        for table in [chat, tool]:
            old[table.name] = [dict(row) for row in db.execute(sa.select(table)).mappings()]
        if seed == 'oi7':
            draft = sa.Table('oi_chat_draft', sa.MetaData(), autoload_with=db)
            values = {c.name: None for c in draft.columns if c.nullable}
            values.update(
                user_id='upgrade-owner',
                chat_id='upgrade-chat',
                revision=1,
                payload={'prompt': 'preserved draft'},
                updated_at=1,
            )
            db.execute(draft.insert().values(**values))
            old[draft.name] = [dict(row) for row in db.execute(sa.select(draft)).mappings()]
command.upgrade(config, 'head')
command.upgrade(config, 'head')
with engine.connect() as db:
    assert db.execute(sa.text('select version_num from alembic_version')).scalar_one() == 'oi0000000008'
    tables = set(sa.inspect(db).get_table_names())
    assert {'comment', 'comment_reaction', 'notification', 'oi_model_call', 'oi_chat_draft'} <= tables
    assert 'is_active' in {c['name'] for c in sa.inspect(db).get_columns('tool')}
    for name, before in old.items():
        table = sa.Table(name, sa.MetaData(), autoload_with=db)
        after = [dict(row) for row in db.execute(sa.select(table)).mappings()]
        assert len(after) == len(before)
        assert [{k: row[k] for k in before[0]} for row in after] == before
    if seed != 'empty':
        assert db.execute(sa.text("select is_active from tool where id='upgrade-tool'")).scalar_one()
print(json.dumps({'seed': seed, 'head': 'oi0000000008', 'preserved_tables': list(old), 'tables': len(tables)}))
engine.dispose()
