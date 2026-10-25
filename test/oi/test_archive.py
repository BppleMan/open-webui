import copy

import pytest
from sqlalchemy import select
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.customization import ChatAnnotation, Generation, MessageRevision, ModelCall
from open_webui.utils.oi import accounting, archive


async def sample(db):
    messages = {
        'question': {
            'id': 'question',
            'role': 'user',
            'content': 'literal RP prompt',
            'parentId': None,
            'childrenIds': ['reply'],
        },
        'reply': {
            'id': 'reply',
            'role': 'assistant',
            'content': 'final reply',
            'parentId': 'question',
            'childrenIds': [],
            'model': 'oi:test',
            'done': True,
        },
    }
    body = {
        'title': 'Roundtrip',
        'models': ['oi:test'],
        'history': {'currentId': 'reply', 'messages': messages},
        'params': {'temperature': 0.7},
    }
    db.add(Chat(id='source', user_id='u', title='Roundtrip', chat=body, created_at=1, updated_at=1))
    await db.flush()
    await ChatMessages.upsert_messages('source', 'u', messages, db=db)
    for index, amount in enumerate(['0.03', '0.04', '0.05']):
        generation, _ = await accounting.begin_generation(db, 'u', 'source', 'reply', f'request-{index}')
        call, _ = await accounting.begin_call(db, generation, 'oi:test')
        await accounting.settle_call(db, call.id, {'input_tokens': 12, 'output_tokens': 3}, actual_usd=amount)
        await accounting.finish_generation(db, generation.id)
    db.add(
        ChatAnnotation(
            user_id='u',
            chat_id='source',
            revision=1,
            data={'bookmarks': {'question': {'label': 'Start', 'created_at': 1}}, 'branch_names': {'reply': 'chosen'}},
            updated_at=1,
        )
    )
    db.add(
        MessageRevision(
            id='revision',
            user_id='u',
            chat_id='source',
            message_id='reply',
            content='old reply',
            output=None,
            created_at=1,
        )
    )
    await db.flush()
    return await archive.export_records(db, 'u', 'source')


@pytest.mark.asyncio
async def test_archive_roundtrip_retains_tree_revisions_annotations_and_deduplicates_charges(db):
    manifest = await sample(db)
    restored = await archive.restore_records(db, 'u', manifest, 'restored')
    assert restored.chat['params'] == manifest['chat']['params']
    assert restored.chat['history']['messages']['question']['content'] == 'literal RP prompt'
    assert (await accounting.get_totals(db, 'restored'))['actual_usd'] == '0.12'
    assert len((await db.execute(select(ModelCall))).scalars().all()) == 3
    assert len((await db.execute(select(Generation))).scalars().all()) == 3
    assert (await db.get(ChatAnnotation, ('u', 'restored'))).data == manifest['annotations']
    revision = (await db.execute(select(MessageRevision).where(MessageRevision.chat_id == 'restored'))).scalar_one()
    assert revision.content == 'old reply'
    await archive.restore_records(db, 'u', manifest, 'another-view')
    assert len((await db.execute(select(ModelCall))).scalars().all()) == 3
    assert (await accounting.get_totals(db, 'another-view'))['actual_usd'] == '0.12'
    assert (await archive.restore_records(db, 'u', manifest, 'restored')).id == 'restored'


@pytest.mark.asyncio
async def test_foreign_import_gets_private_canonical_lineage_and_repeat_import_is_deduplicated(db):
    manifest = await sample(db)
    await archive.restore_records(db, 'v', manifest, 'v-copy', {'oi:test': 'oi:replacement'})
    await archive.restore_records(db, 'v', manifest, 'v-copy-two')
    assert len((await db.execute(select(ModelCall))).scalars().all()) == 6
    assert (await accounting.get_totals(db, 'v-copy'))['actual_usd'] == '0.12'
    normalized = await ChatMessages.get_messages_map_by_chat_id('v-copy', db=db)
    assert normalized['reply']['model'] == 'oi:replacement' and normalized['reply']['oi_native_invalidated']
    exported = await archive.export_records(db, 'v', 'v-copy')
    assert all(g['user_id'] == 'v' for g in exported['billing']['generations'])
    with pytest.raises(PermissionError):
        await archive.export_records(db, 'v', 'source')


@pytest.mark.asyncio
async def test_archive_rejects_cyclic_cost_inheritance_and_identity_rewrites(db):
    manifest = await sample(db)
    malformed = copy.deepcopy(manifest)
    snapshot = malformed['billing']['snapshots'][0]
    snapshot['inherited_snapshot_id'] = snapshot['id']
    with pytest.raises(ValueError, match='Cyclic'):
        archive.validate(malformed)
    await archive.restore_records(db, 'u', manifest, 'copy')
    changed = copy.deepcopy(manifest)
    changed['billing']['calls'][0]['model_id'] = 'altered'
    with pytest.raises(ValueError, match='conflicts'):
        await archive.restore_records(db, 'u', changed, 'other')


@pytest.mark.asyncio
async def test_zip_roundtrip_retains_attachment_bytes_and_remaps_only_file_references(db, tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace
    from open_webui.models.files import File
    from open_webui.utils.oi import archive_files

    await (await db.connection()).run_sync(File.__table__.create)
    manifest = await sample(db)
    data = '真实字节的合成附件，不调用供应商。'.encode()
    source = tmp_path / 'source.txt'
    source.write_bytes(data)
    db.add(
        File(
            id='asset',
            user_id='u',
            filename='scene.txt',
            path=str(source),
            hash=None,
            data={'content': data.decode()},
            meta={'content_type': 'text/plain'},
            created_at=1,
            updated_at=1,
        )
    )
    await db.flush()
    manifest['chat']['history']['messages']['question']['files'] = [
        {'type': 'file', 'id': 'asset', 'url': '/api/v1/files/asset/content', 'collection_name': 'source-server-index'}
    ]
    manifest['chat']['files'] = [{'type': 'file', 'id': 'asset'}]

    class Storage:
        @staticmethod
        def get_file(path):
            return path

        @staticmethod
        def upload_file(stream, name, tags):
            content = stream.read()
            path = tmp_path / name
            path.write_bytes(content)
            return content, str(path)

        @staticmethod
        def delete_file(path):
            Path(path).unlink()

    monkeypatch.setattr(archive_files, 'Storage', Storage)
    path, exported = await archive_files.create_bundle(db, SimpleNamespace(id='u', role='user'), manifest)
    assert exported['attachments'][0]['sha256'] == hashlib.sha256(data).hexdigest()
    try:
        with open(path, 'rb') as stream:
            decoded, bundle = archive_files.read_bundle(stream)
            rewritten, created = await archive_files.restore_files(db, 'v', decoded, bundle)
            bundle.close()
        restored = await archive.restore_records(db, 'v', rewritten, 'restored-asset')
        reference = restored.chat['history']['messages']['question']['files'][0]
        stored = await db.get(File, reference['id'])
        assert Path(stored.path).read_bytes() == data and stored.user_id == 'v'
        assert reference['url'] == f'/api/v1/files/{stored.id}/content'
        assert 'collection_name' not in reference
        assert restored.chat['history']['messages']['question']['content'] == 'literal RP prompt'
        # A surviving DB row is not proof that its backing object still exists.
        missing_path=stored.path
        Path(missing_path).unlink()
        with open(path,'rb') as stream:
            decoded,bundle=archive_files.read_bundle(stream)
            repaired,recreated=await archive_files.restore_files(db,'v',decoded,bundle)
            bundle.close()
        assert stored.path != missing_path and Path(stored.path).read_bytes()==data
        assert len(recreated)==1 and repaired['chat']['history']['messages']['question']['files'][0]['id']==stored.id
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_archive_import_rolls_back_messages_with_later_annotation_failure(db, monkeypatch):
    manifest = await sample(db)
    await db.commit()

    def fail(**kwargs):
        raise ValueError('synthetic late failure')

    monkeypatch.setattr(archive, 'ChatAnnotation', fail)
    with pytest.raises(ValueError, match='synthetic late failure'):
        await archive.restore_records(db, 'u', manifest, 'failed-copy')
    await db.rollback()
    assert await db.get(Chat, 'failed-copy') is None
    assert await db.get(Chat, 'source') is not None
