"""PostgreSQL shared call attribution and legacy archive restore boundaries."""

import asyncio, json, os, uuid
from pathlib import Path
from sqlalchemy import select
from open_webui.internal.db import get_async_db
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.customization import ModelCall
from open_webui.utils.oi import accounting, archive, statistics


async def main():
    cid = str(uuid.uuid4())
    async with get_async_db() as db:
        db.add(Chat(id=cid, user_id='round3-owner', title='Shared', chat={}, created_at=1, updated_at=1))
        await db.commit()

    async def call(actor):
        async with get_async_db() as db:
            generation, _ = await accounting.begin_generation(
                db, actor, cid, actor, actor, ledger_owner_id='round3-owner'
            )
            record, _ = await accounting.begin_call(db, generation, 'round3-model')
            await accounting.settle_call(db, record.id, {'input_tokens': 1, 'output_tokens': 1}, actual_usd='0.03')
            await accounting.finish_generation(db, generation.id)
            await db.commit()

    await asyncio.gather(call('round3-owner'), call('round3-writer'))
    async with get_async_db() as db:
        assert (await accounting.get_totals(db, cid))['actual_usd'] == '0.06'
        assert sorted((await db.execute(select(ModelCall.sequence).where(ModelCall.chat_id == cid))).scalars()) == [
            1,
            2,
        ]
        for actor in ['round3-owner', 'round3-writer']:
            assert (await statistics.by(db, user_id=actor))['round3-model']['count'] == 1
        message = {
            'id': 'legacy',
            'role': 'assistant',
            'content': 'Synthetic legacy',
            'model': 'round3-legacy-model',
            'usage': {'input_tokens': 100, 'output_tokens': 20},
            'done': True,
        }
        legacy = str(uuid.uuid4())
        body = {'history': {'currentId': 'legacy', 'messages': {'legacy': message}}}
        db.add(Chat(id=legacy, user_id='round3-owner', title='Legacy', chat=body, created_at=1, updated_at=1))
        await db.flush()
        await ChatMessages.upsert_message('legacy', legacy, 'round3-owner', message, db=db)
        manifest = await archive.export_records(db, 'round3-owner', legacy)
        for _ in range(2):
            restored = str(uuid.uuid4())
            await archive.restore_records(db, 'round3-owner', manifest, restored)
            assert (await accounting.get_totals(db, restored))['unknown_baselines'] == 1
        assert (await statistics.by(db))['round3-legacy-model']['input_tokens'] == 100
        await accounting.set_baseline(db, 'round3-owner', restored, 'imported-history', actual_usd='0', revision=1)
        assert not (await accounting.get_totals(db, restored))['incomplete']
        await db.commit()
    Path(os.environ['OI_STAGE7_STATE'], 'round3-postgres.json').write_text(
        json.dumps(
            {
                'parallel_shared_calls': 'passed',
                'actor_attribution': 'passed',
                'legacy_archive_unknown_baseline': 'passed',
                'repeated_restore_statistics': 'passed',
                'explicit_zero_baseline': 'passed',
            },
            indent=2,
        )
        + '\n'
    )
    print('Round3 PostgreSQL concurrent actor accounting and legacy archive boundaries passed')


asyncio.run(main())
