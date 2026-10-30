"""Controlled stale discovery on two real PostgreSQL sessions in the stage7 sandbox."""

import asyncio, json, os
from pathlib import Path
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select
from open_webui.internal.db import get_async_db
from open_webui.models.customization import Connection, ModelBinding
from open_webui.routers import customization
from open_webui.utils.oi import catalog


async def main():
    root = Path(os.environ['OI_STAGE7_STATE'])
    user = SimpleNamespace(id='round2-synthetic', role='admin')
    async with get_async_db() as db:
        db.add(
            Connection(
                id='round2-race',
                owner_id=user.id,
                name='Race',
                template='openai-compatible',
                protocol='openai-chat',
                transport='server',
                base_url='https://old.invalid',
                credentials='',
                settings={},
                shared=False,
                enabled=True,
                revision=1,
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            ModelBinding(
                id='oi:round2-alpha',
                connection_id='round2-race',
                upstream_id='alpha',
                protocol='openai-chat',
                name='Alpha',
                enabled=True,
                available=True,
                capabilities={'manual': False},
                pricing={},
                params={},
                aliases=[],
                updated_at=1,
            )
        )
        await db.commit()
        started, release = asyncio.Event(), asyncio.Event()
        original = catalog.fetch_models

        async def delayed(connection, transport=None):
            assert connection.base_url == 'https://old.invalid'
            started.set()
            await release.wait()
            return [{'id': 'alpha'}]

        catalog.fetch_models = delayed
        try:
            pending = asyncio.create_task(customization.discover_models('round2-race', user, db))
            await asyncio.wait_for(started.wait(), 10)
            async with get_async_db() as other:
                current = await other.get(Connection, 'round2-race')
                await customization.save_connection(
                    other,
                    user,
                    customization.ConnectionForm(
                        name='Race',
                        template='openai-compatible',
                        protocol='openai-chat',
                        base_url='https://new.invalid',
                        revision=1,
                    ),
                    current,
                )
                await catalog.apply_discovered_models(other, current, [{'id': 'beta'}])
                await other.commit()
            release.set()
            try:
                await asyncio.wait_for(pending, 10)
            except HTTPException as error:
                assert error.status_code == 409
            else:
                raise AssertionError('Stale discovery was accepted')
            rows = (
                (await db.execute(select(ModelBinding).where(ModelBinding.connection_id == 'round2-race')))
                .scalars()
                .all()
            )
            assert {r.upstream_id: r.available for r in rows} == {'alpha': False, 'beta': True}
        finally:
            catalog.fetch_models = original
            release.set()
    (root / 'round2-postgres.json').write_text(
        json.dumps(
            {'two_independent_sessions': True, 'stale_result_status': 409, 'new_catalog_preserved': True}, indent=2
        )
        + '\n'
    )
    print('Real PostgreSQL stale-discovery race passed; late result rejected with 409')


asyncio.run(main())
