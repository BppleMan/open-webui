"""PostgreSQL shared metadata privacy and portable signed archive replay."""

import asyncio, json, os, uuid
from pathlib import Path
from open_webui.internal.db import get_async_db
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessages
from open_webui.utils.oi import accounting, archive
from open_webui.utils.oi.native_privacy import project_native
import copy


async def main():
    cid = str(uuid.uuid4())
    model = 'round4-gemini'
    owner = 'round4-owner'
    writer = 'round4-writer'
    async with get_async_db() as db:
        chat = Chat(id=cid, user_id=owner, title='Round4 synthetic', chat={}, created_at=1, updated_at=1)
        db.add(chat)
        await db.flush()
        generation, _ = await accounting.begin_generation(
            db, writer, cid, 'reply', str(uuid.uuid4()), ledger_owner_id=owner
        )
        call, _ = await accounting.begin_call(db, generation, model)
        await accounting.settle_call(db, call.id, {'input_tokens': 1, 'output_tokens': 1}, actual_usd='0.03')
        await accounting.finish_generation(db, generation.id)
        message = {
            'id': 'reply',
            'role': 'assistant',
            'user_id': owner,
            'content': 'visible',
            'model': model,
            'done': True,
            'oi_native': [
                {
                    'call_id': call.id,
                    'binding_id': model,
                    'protocol': 'gemini',
                    'body': {
                        'instructions': 'writer-private',
                        'candidates': [
                            {
                                'content': {
                                    'role': 'model',
                                    'parts': [{'text': 'visible', 'thoughtSignature': 'signed-state'}],
                                }
                            }
                        ],
                    },
                }
            ],
        }
        body = {'models': [model], 'history': {'currentId': 'reply', 'messages': {'reply': message}}}
        chat.chat = body
        await ChatMessages.upsert_message('reply', cid, owner, message, db=db)
        await db.commit()
        view = await project_native(copy.deepcopy(body), owner, owner, db)
        assert 'writer-private' not in json.dumps(view)
        manifest = await archive.export_records(db, owner, cid, directory=[{'id': model}])
        assert 'writer-private' not in json.dumps(manifest) and 'signed-state' in json.dumps(manifest)
        restored_id = str(uuid.uuid4())
        await archive.restore_records(db, owner, manifest, restored_id, {model: model})
        restored = await db.get(Chat, restored_id)
        assert 'signed-state' in json.dumps(restored.chat) and 'writer-private' not in json.dumps(restored.chat)
        assert (await accounting.get_totals(db, restored_id))['actual_usd'] == '0.03'
        await db.commit()
    Path(os.environ['OI_STAGE7_STATE'], 'round4-postgres.json').write_text(
        json.dumps(
            {'caller_privacy': 'passed', 'safe_native_archive_restore': 'passed', 'inherited_cost': '0.03'}, indent=2
        )
        + '\n'
    )
    print('Round4 PostgreSQL caller privacy, signed archive and inherited cost passed')


asyncio.run(main())
