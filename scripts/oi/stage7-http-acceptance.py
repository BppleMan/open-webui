"""Real PostgreSQL/HTTP acceptance with synthetic identities, calls and attachment bytes."""

import asyncio, copy, hashlib, io, json, os, pathlib, stat, uuid, zipfile
from decimal import Decimal
import httpx
from sqlalchemy import select, func
from open_webui.internal.db import get_async_db
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.files import File
from open_webui.models.customization import ModelCall, Generation, CostSnapshot, ImportReceipt, ImportCostBaseline
from open_webui.utils.oi import accounting, statistics

p = pathlib.Path(os.environ['OI_STAGE7_STATE'])
api = os.environ['OI_STAGE7_API']
provider = os.environ['OI_STAGE7_PROVIDER']


def bundle(manifest, assets=None, extra=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps(manifest))
        for name, data in (assets or {}).items():
            z.writestr(name, data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return out.getvalue()


async def main():
    async with httpx.AsyncClient(base_url=api, timeout=90, trust_env=False) as c:

        async def req(method, path, body=None, status=200, **kwargs):
            r = await c.request(method, path, json=body, **kwargs)
            assert r.status_code == status, (path, r.status_code, r.text[:500])
            return r.json()

        owner = await req(
            'POST',
            '/api/v1/auths/signup',
            {
                'name': 'Stage7 Owner',
                'email': 'oi-stage7-owner@example.invalid',
                'password': 'stage7-synthetic-password',
            },
        )
        c.headers['Authorization'] = 'Bearer ' + owner['token']
        other = await req(
            'POST',
            '/api/v1/auths/add',
            {
                'name': 'Stage7 Other',
                'email': 'oi-stage7-other@example.invalid',
                'password': 'stage7-synthetic-password',
                'role': 'user',
            },
        )
        other = await req(
            'POST',
            '/api/v1/auths/signin',
            {'email': 'oi-stage7-other@example.invalid', 'password': 'stage7-synthetic-password'},
        )
        await req(
            'POST',
            '/api/v1/configs/import',
            {
                'config': {
                    'task.title.enable': False,
                    'task.tags.enable': False,
                    'task.follow_up.enable': False,
                }
            },
        )
        connection = await req(
            'POST',
            '/api/v1/connections',
            {
                'name': 'Stage7 synthetic',
                'template': 'openai-compatible',
                'protocol': 'openai-chat',
                'base_url': provider + '/v1',
                'shared': True,
                'settings': {'auth': 'none', 'cost_currency': 'USD'},
            },
        )
        model = await req(
            'POST',
            '/api/v1/model-bindings',
            {
                'name': 'Stage7 cost model',
                'connection_id': connection['id'],
                'upstream_id': 'fixture',
                'protocol': 'openai-chat',
                'capabilities': {'chat': True, 'tools': True, 'input_limit': 32768},
            },
        )
        await req('POST', '/api/v1/configs/import', {'config': {'ui.default_models': model['id']}})

        async def mode(**kw):
            r = await c.post(provider + '/mode', json=kw)
            r.raise_for_status()

        async def usage(chat):
            return await req('GET', f'/api/v1/chats/{chat}/usage')

        async def send(chat=None, uid=None, parent=None, kind='assistant', mid=None, tools=False):
            if chat is None:
                chat = str(uuid.uuid4())
                await req('POST', '/api/v1/chats/drafts', {'chat_id': chat})
            uid = uid or str(uuid.uuid4())
            mid = mid or str(uuid.uuid4())
            body = {
                'model': model['id'],
                'chat_id': chat,
                'id': mid,
                'oi_request_id': str(uuid.uuid4()),
                'session_id': 'stage7-synthetic',
                'stream': True,
                'parent_id': parent,
                'messages': [{'role': 'user', 'content': 'Stage7 synthetic conversation.'}],
                'user_message': {
                    'id': uid,
                    'parentId': parent,
                    'role': 'user',
                    'content': 'Stage7 synthetic conversation.',
                    'models': [model['id']],
                },
                'params': {'function_calling': 'native' if tools else 'legacy'},
                'background_tasks': {},
                'oi_generation_kind': kind,
            }
            if kind == 'continue':
                body['assistant_message_id'] = mid
            before = len((await usage(chat))['calls'])
            r = await req('POST', '/api/chat/completions', body)
            for _ in range(100):
                u = await usage(chat)
                history = (await req('GET', f'/api/v1/chats/{chat}'))['chat']['history']['messages']
                if (
                    len(u['calls']) > before
                    and all(x['status'] != 'pending' for x in u['calls'])
                    and history.get(mid, {}).get('done')
                ):
                    break
                await asyncio.sleep(0.1)
            assert len(u['calls']) > before and history.get(mid, {}).get('done'), u
            return chat, uid, mid, body

        # All three generations share a user turn; duplicate provider usage is not extra consumption.
        await mode(cost='0.03')
        chat, uid, m1, body = await send()
        await req('POST', '/api/chat/completions', body, status=409)
        await mode(cost='0.04')
        _, _, m2, _ = await send(chat, uid, kind='regenerate')
        await mode(cost='0.05')
        _, _, m3, _ = await send(chat, uid, kind='regenerate')
        u = await usage(chat)
        assert Decimal(u['totals']['actual_usd']) == Decimal('.12') and len(u['calls']) == 3
        assert all(x['usage']['input_tokens'] == 100 and x['usage']['output_tokens'] == 5 for x in u['calls'])
        fork = await req('POST', f'/api/v1/chats/{chat}/fork', {'message_id': m3})
        await mode(cost='.02')
        _, _, fm, _ = await send(fork['id'], parent=m3)
        assert Decimal((await usage(fork['id']))['totals']['actual_usd']) == Decimal('.14')
        await mode(cost='.20')
        await send(chat, parent=m3)
        assert Decimal((await usage(fork['id']))['totals']['actual_usd']) == Decimal('.14')
        # True late settlement updates only snapshots which include the original call, not parent later calls.
        async with get_async_db() as db:
            await accounting.settle_call(db, u['calls'][0]['id'], {}, actual_usd='.06', revision=2)
            await db.commit()
        assert Decimal((await usage(fork['id']))['totals']['actual_usd']) == Decimal('.17')
        fu = await usage(fork['id'])
        assert all(Decimal(s['actual_usd']) < Decimal('.2') for s in fu['snapshots'])
        # Continue is a distinct generation despite the same assistant message id.
        await mode(cost='.01')
        await send(fork['id'], mid=fm, kind='continue')
        assert Decimal((await usage(fork['id']))['totals']['actual_usd']) == Decimal('.18')
        await mode(cost=None, interrupted=True)
        unknown, _, _, _ = await send()
        uu = await usage(unknown)
        assert uu['totals']['unknown_calls'] == 1 and uu['calls'][0]['usage']['input_tokens'] == 100
        # Explicit background task and native tool follow-up are attributed rather than invisible calls.
        await mode(cost='.01', tool=True)
        toolchat, _, _, _ = await send(tools=True)
        tu = await usage(toolchat)
        assert len(tu['calls']) == 2, tu
        assert Decimal(tu['totals']['actual_usd']) == Decimal('.02')
        await mode(cost='.01')
        await req('POST', '/api/v1/configs/import', {'config': {'task.title.enable': True}})
        await req(
            'POST',
            '/api/v1/tasks/title/completions',
            {'model': model['id'], 'chat_id': toolchat, 'messages': [{'role': 'user', 'content': 'Synthetic title'}]},
        )
        tu = await usage(toolchat)
        assert len(tu['calls']) == 3 and any(
            x['usage']['context']['purpose'] == 'title_generation' for x in tu['calls']
        )
        await req('POST', '/api/v1/configs/import', {'config': {'task.title.enable': False}})
        print('Regen .12 / fork .14 / late / continue / interruption / tool / task PASS', flush=True)
        # Concurrent late settlements must not leave materialized snapshots stale.
        parallel = str(uuid.uuid4())
        await req('POST', '/api/v1/chats/drafts', {'chat_id': parallel})
        parallel_calls = []
        async with get_async_db() as db:
            for i in range(2):
                g, _ = await accounting.begin_generation(db, owner['id'], parallel, str(i), 'parallel-' + str(i))
                call, _ = await accounting.begin_call(db, g, model['id'])
                parallel_calls.append(call.id)
            snapshot = await accounting.finish_generation(db, g.id)
            sid = snapshot.id
            await db.commit()

        async def settle_late(cid, amount):
            async with get_async_db() as db:
                await accounting.settle_call(
                    db, cid, {'input_tokens': 1, 'output_tokens': 1}, actual_usd=amount, revision=2
                )
                await db.commit()

        await asyncio.gather(settle_late(parallel_calls[0], '1'), settle_late(parallel_calls[1], '2'))
        async with get_async_db() as db:
            assert Decimal((await db.get(CostSnapshot, sid)).totals['actual_usd']) == 3
        print('Concurrent settlement materialized snapshot PASS', flush=True)
        provider_count_before = len((await c.get(provider + '/events')).json())
        # Ordinary JSON import, initial historical cost, replacement and race detection.
        history = {
            'q': {
                'id': 'q',
                'role': 'user',
                'content': 'Stage7 imported original',
                'parentId': None,
                'childrenIds': ['a'],
            },
            'a': {
                'id': 'a',
                'parentId': 'q',
                'childrenIds': [],
                'role': 'assistant',
                'content': 'Imported reply',
                'model': model['id'],
                'done': True,
                'usage': {'cost': 999, 'input_tokens': 9, 'output_tokens': 2},
            },
        }
        form = {
            'chat': {
                'title': 'Stage7 JSON history',
                'models': [model['id']],
                'history': {'currentId': 'a', 'messages': history},
            },
            'oi_cost_baseline': {'actual_usd': '12.50'},
        }
        payload = {'import_id': 'stage7-json', 'chats': [form]}
        imported = (await req('POST', '/api/v1/chats/import', payload))[0]
        ic = imported['id']
        assert (await usage(ic))['totals']['actual_usd'] == '12.50' and (await usage(ic))['calls'] == []
        assert (await req('POST', '/api/v1/chats/import', payload))[0]['id'] == ic

        async def baseline_writer(value):
            return await c.put(
                f'/api/v1/chats/{ic}/cost-baselines',
                json={'batch_id': 'imported-history', 'actual_usd': value, 'revision': 1},
            )

        results = await asyncio.gather(*(baseline_writer(str(i)) for i in range(2, 8)))
        assert [r.status_code for r in results].count(200) == 1 and [r.status_code for r in results].count(409) == 5
        b = (await req('GET', f'/api/v1/chats/{ic}/cost-baselines'))[0]
        await req(
            'PUT',
            f'/api/v1/chats/{ic}/cost-baselines',
            {'batch_id': b['batch_id'], 'estimated_usd': '2.25', 'revision': b['revision']},
        )
        await req(
            'POST', '/api/v1/chats/import', {'import_id': 'stage7-json', 'chats': [{**form, 'oi_cost_baseline': None}]}
        )
        assert (await usage(ic))['totals']['estimated_usd'] == '2.25'
        uf = copy.deepcopy(form)
        uf.pop('oi_cost_baseline')
        unknown_import = (await req('POST', '/api/v1/chats/import', {'import_id': 'stage7-unknown', 'chats': [uf]}))[0]
        assert (await usage(unknown_import['id']))['totals']['unknown_baselines'] == 1
        await req(
            'PUT',
            f'/api/v1/chats/{unknown_import["id"]}/cost-baselines',
            {'batch_id': 'imported-history', 'actual_usd': '0', 'revision': 1},
        )
        assert not (await usage(unknown_import['id']))['totals']['incomplete']
        bad = copy.deepcopy(form)
        bad['oi_cost_baseline'] = {'actual_usd': 'NaN'}
        async with get_async_db() as db:
            before = (await db.execute(select(func.count()).select_from(Chat))).scalar_one()
        await req('POST', '/api/v1/chats/import', {'import_id': 'stage7-rollback', 'chats': [form, bad]}, status=422)
        async with get_async_db() as db:
            assert (await db.execute(select(func.count()).select_from(Chat))).scalar_one() == before
        print('JSON baseline / retry / replacement / six writers / unknown vs zero / rollback PASS', flush=True)
        # Archive has all branches plus real uploaded bytes, revision and UI annotations.
        data = 'Stage7 portable attachment 原始字节'.encode()
        r = await c.post('/api/v1/files/?process=false', files={'file': ('scene.txt', data, 'text/plain')})
        r.raise_for_status()
        file_id = r.json()['id']
        async with get_async_db() as db:
            file = await db.get(File, file_id)
            file.data = {'content': data.decode()}
            await db.commit()
        original_bill = (await usage(chat))['totals']
        await req('POST', f'/api/v1/chats/{chat}/messages/{m1}', {'content': 'Edited archived branch before removal.'})
        await req('DELETE', f'/api/v1/chats/{chat}/messages/{m1}')
        await req(
            'POST',
            f'/api/v1/chats/{chat}/messages/{m3}',
            {'content': 'Edited chosen branch; original revision retained.'},
        )
        assert (await usage(chat))['totals'] == original_bill
        saved = await req('GET', f'/api/v1/chats/{chat}')
        tree = saved['chat']['history']
        tree['messages'][uid]['files'] = [
            {
                'type': 'file',
                'id': file_id,
                'name': 'scene.txt',
                'size': len(data),
                'content_type': 'text/plain',
                'status': 'uploaded',
                'url': f'/api/v1/files/{file_id}/content',
                'collection_name': 'not-portable',
            }
        ]
        await req('POST', f'/api/v1/chats/{chat}', {'chat': {'history': tree}})
        await req(
            'PUT',
            f'/api/v1/chats/{chat}/annotations',
            {
                'revision': 0,
                'data': {'bookmarks': {uid: {'label': 'opening', 'created_at': 1}}, 'branch_names': {m3: 'chosen'}},
            },
        )
        r = await c.get(f'/api/v1/chats/{chat}/archive-bundle')
        r.raise_for_status()
        archive_bytes = r.content
        (p / 'source.zip').write_bytes(archive_bytes)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
            manifest = json.loads(z.read('manifest.json'))
            assets = {n: z.read(n) for n in z.namelist() if n != 'manifest.json'}
        assert manifest['attachments'][0]['sha256'] == hashlib.sha256(data).hexdigest()
        assert len(manifest['chat']['history']['messages']) >= 5
        assert len(manifest['revisions']) >= 2
        assert m1 not in manifest['chat']['history']['messages']
        assert any(revision['message_id'] == m1 for revision in manifest['revisions'])
        assert 'credentials' not in json.dumps(manifest['model_references'])

        async def restore(key, token=owner['token'], blob=archive_bytes, mapping=None):
            return await c.post(
                '/api/v1/archives/import',
                headers={'Authorization': 'Bearer ' + token},
                data={'import_id': key, 'model_mapping': json.dumps(mapping or {})},
                files={'file': ('archive.zip', blob, 'application/zip')},
            )

        key = str(uuid.uuid4())
        responses = await asyncio.gather(*(restore(key) for _ in range(6)))
        assert all(r.status_code == 200 for r in responses), [(r.status_code, r.text[:150]) for r in responses]
        restored = responses[0].json()['id']
        assert len({r.json()['id'] for r in responses}) == 1
        rr = await restore(str(uuid.uuid4()))
        assert rr.status_code == 200, rr.text
        expected = (await usage(chat))['totals']['actual_usd']
        assert (await usage(restored))['totals']['actual_usd'] == expected
        replacement = await req(
            'POST',
            '/api/v1/model-bindings',
            {
                'name': 'Stage7 replacement model',
                'connection_id': connection['id'],
                'upstream_id': 'replacement',
                'protocol': 'openai-chat',
                'capabilities': {'chat': True},
            },
        )
        mapped = await restore(str(uuid.uuid4()), mapping={model['id']: replacement['id']})
        assert mapped.status_code == 200, mapped.text
        mapped_usage = await usage(mapped.json()['id'])
        assert all(
            call['model_id'] == model['id'] and call['view_model_id'] == replacement['id']
            for call in mapped_usage['calls']
        )
        assert mapped_usage['totals']['actual_usd'] == expected
        imported_bundle = await c.get(f'/api/v1/chats/{restored}/archive-bundle')
        imported_bundle.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(imported_bundle.content)) as z:
            m = json.loads(z.read('manifest.json'))
            assert z.read(m['attachments'][0]['blob']) == data
            assert len(m['revisions']) == len(manifest['revisions'])
            assert m['annotations'] == manifest['annotations']
        foreign = await restore(str(uuid.uuid4()), other['token'])
        assert foreign.status_code == 200, foreign.text
        foreignid = foreign.json()['id']
        denied = await c.get(
            f'/api/v1/chats/{chat}/archive-bundle', headers={'Authorization': 'Bearer ' + other['token']}
        )
        assert denied.status_code in [403, 404]
        denied = await c.get(f'/api/v1/chats/{chat}/usage', headers={'Authorization': 'Bearer ' + other['token']})
        assert denied.status_code in [403, 404]
        own_foreign = await c.get(f'/api/v1/chats/{foreignid}', headers={'Authorization': 'Bearer ' + other['token']})
        own_foreign.raise_for_status()
        fref = own_foreign.json()['chat']['history']['messages'][uid]['files'][0]
        assert fref['name'] == 'scene.txt' and fref['size'] == len(data)
        rf = await c.get(f'/api/v1/files/{fref["id"]}/content', headers={'Authorization': 'Bearer ' + other['token']})
        assert rf.status_code == 200 and rf.content == data
        assert 'collection_name' not in fref
        async with get_async_db() as db:
            totals = await statistics.by(db)
            canonical_calls = sum(x['count'] for x in totals.values())
            original_calls = (
                await db.execute(
                    select(func.count())
                    .select_from(ModelCall)
                    .join(Generation, Generation.id == ModelCall.generation_id)
                    .where(Generation.user_id == owner['id'])
                )
            ).scalar_one()
            assert canonical_calls == original_calls, (canonical_calls, original_calls)
        # Permission-denied attachments are explicit and never carry bytes or filenames.
        forbidden = copy.deepcopy(form)
        forbidden['chat']['history']['messages']['q']['files'] = [
            {'type': 'file', 'id': file_id, 'url': f'/api/v1/files/{file_id}/content'}
        ]
        r = await c.post(
            '/api/v1/chats/import',
            headers={'Authorization': 'Bearer ' + other['token']},
            json={'import_id': 'foreign-ref', 'chats': [forbidden]},
        )
        assert r.status_code == 200, r.text
        no_access = r.json()[0]['id']
        r = await c.get(
            f'/api/v1/chats/{no_access}/archive-bundle', headers={'Authorization': 'Bearer ' + other['token']}
        )
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            unavailable = json.loads(z.read('manifest.json'))['attachments']
            assert unavailable == [{'id': file_id, 'status': 'unavailable'}] and z.namelist() == ['manifest.json']
        from open_webui.models.config import Config

        permissions = await Config.get('user.permissions')
        await req(
            'POST',
            '/api/v1/configs/import',
            {'config': {'user.permissions': {'chat': {'import': False, 'export': False}}}},
        )
        r = await c.post(
            '/api/v1/archives/preview',
            headers={'Authorization': 'Bearer ' + other['token']},
            files={'file': ('archive.zip', archive_bytes, 'application/zip')},
        )
        assert r.status_code == 403
        r = await c.get(
            f'/api/v1/chats/{foreignid}/archive-bundle', headers={'Authorization': 'Bearer ' + other['token']}
        )
        assert r.status_code == 403
        await req('POST', '/api/v1/configs/import', {'config': {'user.permissions': permissions}})
        # Bad manifest/file entries must be rejected without creating chats or files.
        variants = []
        malformed = copy.deepcopy(manifest)
        malformed['billing']['calls'][0]['sequence'] = 0
        variants.append(bundle(malformed, assets))
        malformed = copy.deepcopy(manifest)
        malformed['attachments'][0]['sha256'] = '0' * 64
        variants.append(bundle(malformed, assets))
        malformed = copy.deepcopy(manifest)
        malformed['attachments'] *= 2
        variants.append(bundle(malformed, assets))
        variants.append(bundle(manifest, assets, {'../escape': 'bad'}))
        async with get_async_db() as db:
            counts = [
                (await db.execute(select(func.count()).select_from(cls))).scalar_one()
                for cls in [Chat, File, ModelCall]
            ]
        for blob in variants:
            r = await restore(str(uuid.uuid4()), blob=blob)
            assert r.status_code == 400, r.text[:200]
        async with get_async_db() as db:
            assert counts == [
                (await db.execute(select(func.count()).select_from(cls))).scalar_one()
                for cls in [Chat, File, ModelCall]
            ]
        print(
            'ZIP bytes / branches / repeated and six concurrent restores / permissions / malformed archives / canonical statistics PASS',
            flush=True,
        )
        assert len((await c.get(provider + '/events')).json()) == provider_count_before
        report = {
            'no_inference_for_import_export_ui_metadata': True,
            'revisions_and_deleted_branch_billing': 'passed',
            'regen_total': '.12',
            'fork_total': '.14',
            'late_correction_fork': '.17',
            'continued_fork': '.18',
            'json_import': ic,
            'archive_source': chat,
            'archive_restored': restored,
            'concurrent_restores': 6,
            'baseline_winners': 1,
            'malformed_rejected': len(variants),
            'canonical_calls': canonical_calls,
            'original_bytes_sha256': hashlib.sha256(data).hexdigest(),
            'production': 'unchanged',
        }
        (p / 'acceptance.json').write_text(json.dumps(report, indent=2))
        (p / 'seed.json').write_text(
            json.dumps({'owner': owner, 'other': other, 'model': model, 'report': report}, indent=2)
        )
        (p / 'seed.json').chmod(0o600)
        (p / 'json-fixture.json').write_text(json.dumps([form], ensure_ascii=False))


asyncio.run(main())
