"""Synthetic populated OI6 -> OI7 fixture, shared by SQLite and PostgreSQL acceptance."""

from types import SimpleNamespace
import sqlalchemy as sa
from open_webui.utils.oi.endpoints import resolve_endpoint

CASES = [
    ('openai-compatible', 'openai-chat', 'https://synthetic.invalid/proxy/v1', {}),
    ('openai-compatible', 'gemini', 'https://synthetic.invalid/proxy/v1beta', {}),
    ('openrouter', 'openai-chat', 'https://openrouter.ai/api/v1', {'auth': 'bearer'}),
    ('cpa-agy', 'cpa-agy', 'https://synthetic.invalid/proxy/v1', {}),
    ('openai-compatible', 'openai-chat', 'https://synthetic.invalid/opaque', {}),
    ('gemini', 'openai-chat', 'https://synthetic.invalid/v1beta/openai', {}),
    ('zai', 'openai-chat', 'https://synthetic.invalid/api/paas/v4', {}),
    (
        'azure-openai',
        'openai-chat',
        'https://synthetic.invalid/proxy/v1',
        {'api_version': '2024-06-01', 'deployment': 'synthetic'},
    ),
    ('ollama', 'ollama', 'https://synthetic.invalid/proxy', {}),
]


def expected(index, override=False):
    template, protocol, address, settings = CASES[index]
    mode = 'anthropic' if override else protocol
    c = SimpleNamespace(template=template, protocol=protocol, base_url=address, settings=settings)
    return resolve_endpoint(c, mode, model='synthetic-model')


def seed(db):
    metadata = sa.MetaData()
    connection = sa.Table('oi_connection', metadata, autoload_with=db)
    binding = sa.Table('oi_model_binding', metadata, autoload_with=db)
    for index, (template, mode, address, settings) in enumerate(CASES):
        cid = f'route-fixture-{index}'
        db.execute(
            connection.insert().values(
                id=cid,
                owner_id='fixture',
                name=cid,
                template=template,
                protocol=mode,
                transport='server',
                base_url=address,
                credentials='',
                settings=settings,
                shared=False,
                enabled=True,
                revision=1,
                created_at=1,
                updated_at=1,
            )
        )
        for override in [False, True]:
            db.execute(
                binding.insert().values(
                    id=f'oi:{cid}-{int(override)}',
                    connection_id=cid,
                    upstream_id='synthetic-model',
                    protocol='anthropic' if override else mode,
                    name='unchanged',
                    enabled=True,
                    available=True,
                    capabilities={},
                    pricing={},
                    params={'temperature': 0.7},
                    aliases=['historic-alias'],
                    updated_at=1,
                )
            )


def verify(db):
    metadata = sa.MetaData()
    connection = sa.Table('oi_connection', metadata, autoload_with=db)
    binding = sa.Table('oi_model_binding', metadata, autoload_with=db)
    for index, (template, old_mode, address, settings) in enumerate(CASES):
        cid = f'route-fixture-{index}'
        c = SimpleNamespace(**db.execute(sa.select(connection).where(connection.c.id == cid)).mappings().one())
        for override in [False, True]:
            b = db.execute(sa.select(binding).where(binding.c.id == f'oi:{cid}-{int(override)}')).mappings().one()
            assert b['protocol_override'] == ('anthropic' if override else None)
            assert b['name'] == 'unchanged' and b['params'] == {'temperature': 0.7}
            mode = b['protocol_override'] or c.protocol
            assert resolve_endpoint(c, mode, model='synthetic-model') == expected(index, override), (index, override)
        if address.endswith('/opaque'):
            assert c.settings['routing']['needs_review'] and c.base_url == address
