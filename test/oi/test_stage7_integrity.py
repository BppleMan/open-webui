import copy
import io
import json
import stat
import zipfile
from decimal import Decimal
import pytest
from open_webui.models.customization import CostSnapshot
from open_webui.utils.oi import accounting, archive, archive_files
from test.oi.test_archive import sample


@pytest.mark.asyncio
async def test_late_cost_only_settlement_keeps_confirmed_usage(db):
    g, _ = await accounting.begin_generation(db, 'u', 'c', 'm', 'r')
    c, _ = await accounting.begin_call(db, g, 'model', pricing={'rates': {'input': '1', 'output': '2'}})
    await accounting.settle_call(db, c.id, {'input_tokens': 100, 'output_tokens': 20}, status='interrupted')
    await accounting.finish_generation(db, g.id, 'interrupted')
    await accounting.settle_call(db, c.id, {}, actual_usd='0.03', revision=2)
    assert c.usage['input_tokens'] == 100 and c.usage['output_tokens'] == 20
    assert c.actual_usd == '0.03' and c.estimated_usd is None


@pytest.mark.asyncio
async def test_baseline_replacement_corrects_materialized_inherited_snapshots(db):
    await accounting.set_baseline(db, 'u', 'c', 'history', actual_usd='1')
    g, _ = await accounting.begin_generation(db, 'u', 'c', 'm', 'r')
    call, _ = await accounting.begin_call(db, g, 'model')
    await accounting.settle_call(db, call.id, {}, actual_usd='0.03')
    snap = await accounting.finish_generation(db, g.id)
    await accounting.inherit_snapshot(db, 'u', 'fork', 'c', 'm')
    fg, _ = await accounting.begin_generation(db, 'u', 'fork', 'fm', 'fr')
    fs = await accounting.finish_generation(db, fg.id)
    await accounting.set_baseline(db, 'u', 'c', 'history', estimated_usd='2')
    assert Decimal(snap.totals['estimated_usd']) == 2
    assert Decimal(fs.totals['estimated_usd']) == 2
    assert (await accounting.get_totals(db, 'fork'))['actual_usd'] == '0.03'


@pytest.mark.parametrize('bad', ['1e100', '0.' + '1' * 100])
def test_money_is_bounded_without_decimal_rounding(bad):
    with pytest.raises(ValueError):
        accounting.money(bad)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['sequence', 'dangling_parent', 'cross_baseline', 'revision'])
async def test_archive_rejects_broken_relations_before_restore(db, case):
    m = await sample(db)
    if case == 'sequence':
        m['billing']['calls'][1]['sequence'] = m['billing']['calls'][0]['sequence']
    elif case == 'dangling_parent':
        m['chat']['history']['messages']['reply']['parentId'] = 'not-present'
    elif case == 'cross_baseline':
        m['billing']['ledgers'].append({'chat_id': 'other', 'user_id': 'u', 'sequence': 0})
        m['billing']['baselines'].append(
            {'id': 'b', 'chat_id': 'other', 'batch_id': 'x', 'actual_usd': '1', 'estimated_usd': None}
        )
        m['billing']['snapshots'][0]['baseline_ids'] = ['b']
    else:
        m['revisions'][0]['created_at'] = 'not-a-timestamp'
    with pytest.raises(ValueError):
        archive.validate(m)


@pytest.mark.asyncio
async def test_bundle_rejects_unlisted_path_traversal_entries(db):
    m = await sample(db)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as z:
        z.writestr('manifest.json', json.dumps(m))
        z.writestr('../escape', 'ignored is not a valid archive')
    with pytest.raises(ValueError):
        archive_files.read_bundle(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['symlink', 'duplicate_manifest', 'size_limit'])
async def test_bundle_rejects_unsafe_members_and_bounded_content(db, monkeypatch, case):
    m = await sample(db)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as z:
        z.writestr('manifest.json', json.dumps(m))
        if case == 'symlink':
            info = zipfile.ZipInfo('link')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, '/outside/archive')
        elif case == 'duplicate_manifest':
            with pytest.warns(UserWarning, match='Duplicate name'):
                z.writestr('manifest.json', json.dumps(m))
        else:
            monkeypatch.setattr(archive_files, 'MAX_MANIFEST', 8)
    with pytest.raises(ValueError):
        archive_files.read_bundle(raw)


@pytest.mark.asyncio
async def test_archive_reexport_keeps_original_call_identity_across_accounts(db):
    from sqlalchemy import select
    from open_webui.models.customization import ModelCall, Generation

    m = await sample(db)
    await archive.restore_records(db, 'v', m, 'v-copy')
    forwarded = await archive.export_records(db, 'v', 'v-copy')
    await archive.restore_records(db, 'w', m, 'w-original')
    await archive.restore_records(db, 'w', forwarded, 'w-forwarded')
    calls = (
        (
            await db.execute(
                select(ModelCall)
                .join(Generation, Generation.id == ModelCall.generation_id)
                .where(Generation.user_id == 'w')
            )
        )
        .scalars()
        .all()
    )
    assert len(calls) == 3
    assert (await accounting.get_totals(db, 'w-forwarded'))['actual_usd'] == '0.12'


@pytest.mark.asyncio
async def test_restored_model_aliases_follow_view_without_rewriting_billing_identity(db, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from open_webui.routers import customization

    original = await sample(db)
    await archive.restore_records(db, 'v', original, 'v-copy', {'oi:test': 'oi:second'})
    forwarded = await archive.export_records(db, 'v', 'v-copy')
    restored = await archive.restore_records(db, 'w', forwarded, 'w-copy', {'oi:second': 'oi:third'})
    monkeypatch.setattr(customization, 'chat_access', AsyncMock(return_value=restored))
    usage = await customization.chat_usage('w-copy', SimpleNamespace(id='w'), db)
    assert usage['totals']['actual_usd'] == '0.12'
    assert all(c['model_id'] == 'oi:test' and c['view_model_id'] == 'oi:third' for c in usage['calls'])
    assert restored.chat['history']['messages']['reply']['model'] == 'oi:third'


@pytest.mark.asyncio
async def test_incremental_archive_settlement_preserves_old_anchor_and_advances_source_boundary(db):
    from open_webui.models.customization import ChatLedger

    m = await sample(db)
    await archive.restore_records(db, 'v', m, 'v-old')
    await accounting.settle_call(db, m['billing']['calls'][0]['id'], {}, actual_usd='.06', revision=2)
    g, _ = await accounting.begin_generation(db, 'u', 'source', 'reply', 'later')
    call, _ = await accounting.begin_call(db, g, 'oi:test')
    await accounting.settle_call(db, call.id, {}, actual_usd='.01')
    await accounting.finish_generation(db, g.id)
    newer = await archive.export_records(db, 'u', 'source')
    await archive.restore_records(db, 'v', newer, 'v-new')
    assert Decimal((await accounting.get_totals(db, 'v-old'))['actual_usd']) == Decimal('.15')
    assert Decimal((await accounting.get_totals(db, 'v-new'))['actual_usd']) == Decimal('.16')
    assert (await db.get(ChatLedger, archive.canonical_id('v', 'ledgers', 'source'))).sequence == 4
    await archive.restore_records(db, 'v', m, 'v-old-again')
    assert Decimal((await accounting.get_totals(db, 'v-old-again'))['actual_usd']) == Decimal('.15')


def test_exact_decimal_sum_exceeds_default_decimal_context():
    from types import SimpleNamespace

    value = '12345678901234567890123.123456789012345678901234567890123456'
    values = {str(i): SimpleNamespace(actual_usd=v, estimated_usd=None) for i, v in enumerate([value, '1'])}
    assert accounting.totals(values, {})['actual_usd'] == '12345678901234567890124.123456789012345678901234567890123456'


@pytest.mark.asyncio
async def test_json_import_receipt_preserves_edited_baseline_and_actor_ownership(db, monkeypatch):
    from unittest.mock import AsyncMock
    from open_webui.models.chats import Chats, ChatImportForm
    from open_webui.utils.oi.imports import import_json
    from open_webui.models.customization import ImportCostBaseline
    from sqlalchemy import select

    monkeypatch.setattr(Chats, 'require_chat_creation_permission', AsyncMock())
    form = ChatImportForm(
        chat={'title': 'json', 'messages': [{'id': 'q', 'role': 'user', 'content': 'original', 'user_id': 'foreign'}]},
        oi_cost_baseline={'actual_usd': '2'},
    )
    first = (await import_json(db, 'u', [form], 'json-key'))[0]
    await accounting.set_baseline(db, 'u', first.id, 'imported-history', actual_usd='3')
    again = (await import_json(db, 'u', [form.model_copy(update={'oi_cost_baseline': None})], 'json-key'))[0]
    assert again.id == first.id and again.chat['history']['messages']['q']['user_id'] == 'u'
    assert (await accounting.get_totals(db, first.id))['actual_usd'] == '3'
    assert len((await db.execute(select(ImportCostBaseline))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_json_import_failure_rolls_back_prior_chats_in_same_batch(db, monkeypatch):
    from unittest.mock import AsyncMock
    from open_webui.models.chats import Chats, ChatImportForm, Chat
    from open_webui.utils.oi.imports import import_json
    from sqlalchemy import select

    monkeypatch.setattr(Chats, 'require_chat_creation_permission', AsyncMock())
    form = ChatImportForm(chat={'messages': [{'id': 'q', 'role': 'user', 'content': 'original'}]})
    bad = form.model_copy(update={'oi_cost_baseline': {'actual_usd': 'NaN'}})
    with pytest.raises(ValueError):
        await import_json(db, 'u', [form, bad], 'bad-import')
    await db.rollback()
    assert not (await db.execute(select(Chat))).scalars().all()


def test_foreign_currency_is_not_silently_reported_as_usd():
    assert (
        accounting.estimate(
            {'input_tokens': 100, 'output_tokens': 5}, {'currency': 'EUR', 'rates': {'input': '1', 'output': '2'}}
        )
        is None
    )


@pytest.mark.asyncio
async def test_archive_recomputes_materialized_totals_instead_of_trusting_them(db):
    from open_webui.models.customization import CostSnapshot

    m = await sample(db)
    for snap in m['billing']['snapshots']:
        snap['totals'] = {'actual_usd': '999999'}
    await archive.restore_records(db, 'v', m, 'v-untrusted-total')
    for source in m['billing']['snapshots']:
        snap = await db.get(CostSnapshot, archive.canonical_id('v', 'snapshots', source['id']))
        assert Decimal(snap.totals['actual_usd']) < 1


@pytest.mark.asyncio
async def test_malicious_usage_cannot_poison_global_statistics(db):
    manifest = await sample(db)
    manifest['billing']['calls'][0]['usage']['input_tokens'] = 'NaN'
    with pytest.raises(ValueError, match='usage counter'):
        archive.validate(manifest)
