"""Authenticated OI catalog, draft, billing and navigation APIs."""

from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import File as UploadField, Form, UploadFile
from open_webui.internal.db import get_async_session
from open_webui.models.chats import ChatForm, Chats
from open_webui.models.customization import (
    ChatAnnotation,
    ChatLedger,
    Connection,
    CostSnapshot,
    Generation,
    ImportCostBaseline,
    MessageRevision,
    ModelBinding,
    ModelCall,
)
from open_webui.utils.auth import get_verified_user
from open_webui.utils.oi import accounting, catalog, drafts
from open_webui.utils.oi.storage import identifier, insert_once, now
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


async def archive_models(request, user, db):
    from open_webui.utils.models import get_all_models, get_filtered_models

    models = list((getattr(request.app.state, 'MODELS', {}) or {}).values())
    if not models:
        models = await get_all_models(request, user=user)
    return await get_filtered_models(models, user, db=db)


router = APIRouter()


@router.get('/chats/{chat_id}/archive-bundle')
async def download_archive(
    chat_id: str, request: Request, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    from pathlib import Path
    from starlette.background import BackgroundTask
    from starlette.responses import FileResponse
    from open_webui.models.config import Config
    from open_webui.utils.access_control import has_permission
    from open_webui.utils.oi import archive, archive_files

    if user.role != 'admin' and not await has_permission(
        user.id, 'chat.export', await Config.get('user.permissions'), db=db
    ):
        raise HTTPException(403, 'Conversation export is disabled')
    try:
        manifest = await archive.export_records(db, user.id, chat_id, await archive_models(request, user, db))
        path, manifest = await archive_files.create_bundle(db, user, manifest)
        unavailable = sum(item['status'] != 'included' for item in manifest['attachments'])
        return FileResponse(
            path,
            media_type='application/zip',
            filename=f'oi-conversation-{chat_id}.zip',
            headers={
                'X-OI-Archive-Unavailable': str(unavailable),
                'X-OI-Archive-External': str(len(manifest['external_references'])),
            },
            background=BackgroundTask(Path(path).unlink, missing_ok=True),
        )
    except PermissionError as error:
        raise HTTPException(403, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@router.post('/archives/preview')
async def preview_archive(
    request: Request,
    file: UploadFile = UploadField(...),
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    import asyncio
    from open_webui.routers.chats import require_chat_import_permission
    from open_webui.utils.oi import archive_files

    await require_chat_import_permission(request, user, db)
    try:
        manifest, bundle = await asyncio.to_thread(archive_files.read_bundle, file.file)
        bundle.close()
        models = await archive_models(request, user, db)
        available = {model['id'] for model in models}
        return {
            'schema': manifest['schema'],
            'title': manifest['chat'].get('title'),
            'message_count': len(manifest['chat'].get('history', {}).get('messages', {})),
            'call_count': len(manifest['billing'].get('calls', [])),
            'revision_count': len(manifest.get('revisions', [])),
            'attachments': [
                {'id': item['id'], 'filename': item.get('filename'), 'status': item['status']}
                for item in manifest.get('attachments', [])
            ],
            'external_reference_count': len(manifest.get('external_references', [])),
            'model_references': [
                {**reference, 'available': reference['id'] in available}
                for reference in manifest.get('model_references', [])
            ],
        }
    except Exception as error:
        # Parser details are not needed to expose local storage paths to a malformed upload.
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(400, 'Unable to read this OI conversation archive') from None


@router.post('/archives/import')
async def import_archive(
    request: Request,
    file: UploadFile = UploadField(...),
    import_id: UUID = Form(...),
    model_mapping: str = Form('{}'),
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    import asyncio
    import json
    from open_webui.routers.chats import require_chat_import_permission
    from open_webui.utils.oi import archive, archive_files

    await require_chat_import_permission(request, user, db)
    created = []
    bundle = None
    try:
        mapping = json.loads(model_mapping)
        if not isinstance(mapping, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()
        ):
            raise ValueError('Model mapping must be an object of model IDs')
        allowed = {model['id'] for model in await archive_models(request, user, db)}
        if any(value not in allowed for value in mapping.values()):
            raise ValueError('A selected destination model is unavailable')
        manifest, bundle = await asyncio.to_thread(archive_files.read_bundle, file.file)
        manifest, created = await archive_files.restore_files(db, user.id, manifest, bundle)
        chat = await archive.restore_records(
            db, user.id, manifest, archive.canonical_id(user.id, 'import', str(import_id)), mapping
        )
        await db.commit()
        return {
            'id': chat.id,
            'title': chat.title,
            'unmapped_models': [
                reference['id']
                for reference in manifest.get('model_references', [])
                if mapping.get(reference['id'], reference['id']) not in allowed
            ],
        }
    except BaseException as error:
        await db.rollback()
        await archive_files.cleanup(created)
        import logging

        logging.getLogger(__name__).warning('archive_restore_failed kind=%s', type(error).__name__)
        if isinstance(error, HTTPException):
            raise
        if not isinstance(error, Exception):
            raise
        raise HTTPException(
            400,
            str(error)
            if isinstance(error, (ValueError, PermissionError))
            else 'Unable to restore this conversation archive',
        ) from None
    finally:
        if bundle is not None:
            bundle.close()


async def chat_access(db, user, chat_id, write=False):
    chat = await Chats.get_accessible_chat_by_id(chat_id, user, db=db, permission='write' if write else 'read')
    if chat is None:
        raise HTTPException(404, 'Conversation unavailable')
    return chat


async def connection_access(db, user, connection_id, write=False):
    row = await db.get(Connection, connection_id)
    if row is None or not (catalog.editable(row, user) if write else catalog.visible(row, user)):
        raise HTTPException(404, 'Connection unavailable')
    return row


class ConnectionForm(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    template: str
    protocol: str | None = None
    transport: Literal['server', 'browser'] = 'server'
    base_url: str | None = None
    api_key: SecretStr | None = None
    headers: dict[str, SecretStr] | None = None
    settings: dict[str, Any] | None = None
    shared: bool = False
    enabled: bool = True
    revision: int | None = None


@router.get('/connections/templates')
async def templates(user=Depends(get_verified_user)):
    return catalog.TEMPLATES


@router.post('/connections/import-legacy')
async def import_legacy(
    request: Request, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    raise HTTPException(409, 'Preview the legacy configuration migration before applying it')


class MigrationPreviewForm(BaseModel):
    scope: Literal['personal', 'server']


class MigrationChoice(BaseModel):
    source_id: str
    action: Literal['create', 'reuse']
    connection_id: str | None = None
    acknowledge_duplicate: bool = False


class MigrationApplyForm(MigrationPreviewForm):
    version: str = Field(min_length=64, max_length=64)
    choices: list[MigrationChoice] = Field(min_length=1, max_length=1000)


@router.post('/connections/migration/preview')
async def migration_preview(
    form: MigrationPreviewForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    from open_webui.utils.oi.connection_migrations import preview

    return await preview(db, user, form.scope)


@router.post('/connections/migration/apply')
async def migration_apply(
    form: MigrationApplyForm,
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    from open_webui.utils.oi.connection_migrations import apply

    try:
        result = await apply(db, user, form.scope, form.version, [choice.model_dump() for choice in form.choices])
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    try:
        if form.scope == 'server':
            # Reload lazily-bound retrieval callables; no embedding or generation is performed here.
            from open_webui.models.config import Config
            from open_webui.retrieval.utils import get_embedding_function, get_reranking_function

            c = await Config.get_many(
                'rag.embedding_engine',
                'rag.embedding_model',
                'rag.embedding_batch_size',
                'rag.reranking_engine',
                'rag.reranking_model',
            )
            if c.get('rag.embedding_engine') == 'connection':
                request.app.state.EMBEDDING_FUNCTION = get_embedding_function(
                    'connection', c['rag.embedding_model'], None, '', '', c.get('rag.embedding_batch_size') or 1
                )
            if c.get('rag.reranking_engine') == 'connection':
                request.app.state.RERANKING_FUNCTION = get_reranking_function(
                    'connection', c['rag.reranking_model'], None
                )
    except Exception:
        # The transaction is already committed. Never report an applied batch as rolled back.
        result = {**result, 'warnings': ['Migration saved; restart the application to reload retrieval configuration.']}
    return result


@router.get('/connections')
async def connections(
    include_deleted: bool = False, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    rows = (await db.execute(select(Connection).order_by(Connection.created_at))).scalars()
    return [
        catalog.connection_json(row, user)
        for row in rows
        if catalog.visible(row, user) or (include_deleted and catalog.editable(row, user))
    ]


async def set_connection_removed(db, user, connection_id, revision, removed):
    row = await connection_access(db, user, connection_id, True)
    result = await db.execute(
        update(Connection)
        .where(Connection.id == row.id, Connection.revision == revision)
        .values(
            settings={**row.settings, 'deleted': removed},
            enabled=False,
            revision=Connection.revision + 1,
            updated_at=catalog.now(),
        )
        .returning(Connection.id)
        .execution_options(synchronize_session=False)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(409, 'Connection changed; reload before updating')
    await db.commit()
    await db.refresh(row)
    return catalog.connection_json(row, user)


@router.delete('/connections/{connection_id}')
async def remove_connection(
    connection_id: str, revision: int, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    return await set_connection_removed(db, user, connection_id, revision, True)


@router.post('/connections/{connection_id}/restore')
async def restore_connection(
    connection_id: str, revision: int, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    return await set_connection_removed(db, user, connection_id, revision, False)


async def save_connection(db, user, form, row=None):
    if row is not None and form.transport != (row.transport or 'server'):
        raise HTTPException(409, 'Use the explicit transport migration flow to change request execution')
    if form.transport == 'browser' and form.shared:
        raise HTTPException(422, 'Browser connections belong to their owner and cannot be shared')
    if form.shared and user.role != 'admin':
        raise HTTPException(403, 'Only administrators may publish shared connections')
    try:
        defaults = catalog.connection_defaults(form, row)
        if form.transport == 'browser' and defaults['protocol'] not in {'openai-chat', 'openai-responses'}:
            raise ValueError('Browser connections currently support OpenAI Chat and Responses protocols')
        url = catalog.validate_url(defaults['base_url'])
        secrets = catalog.credentials(row) if row else {}
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if form.api_key is not None:
        secrets['api_key'] = form.api_key.get_secret_value()
    if form.headers is not None:
        secrets['headers'] = {key: value.get_secret_value() for key, value in form.headers.items()}
    values = {key: getattr(form, key) for key in ('name', 'template', 'shared', 'enabled', 'transport')}
    values.update(defaults)
    values['name'] = values['name'].strip()
    if not values['name']:
        raise HTTPException(422, 'Connection name is required')
    values.update(base_url=url, credentials=catalog.encrypt_credentials(secrets) if secrets else '', updated_at=now())
    if row is None:
        row = Connection(id=identifier(), owner_id=user.id, created_at=now(), revision=1, **values)
        db.add(row)
    else:
        result = await db.execute(
            update(Connection)
            .where(Connection.id == row.id, Connection.revision == form.revision)
            .values(**values, revision=Connection.revision + 1)
            .returning(Connection.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(409, 'Connection changed; reload before saving')
        await db.refresh(row)
    await db.commit()
    return catalog.connection_json(row, user)


@router.post('/connections')
async def create_connection(
    form: ConnectionForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    return await save_connection(db, user, form)


@router.put('/connections/{connection_id}')
async def update_connection(
    connection_id: str,
    form: ConnectionForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    row = await connection_access(db, user, connection_id, True)
    return await save_connection(db, user, form, row)


@router.post('/connections/{connection_id}/discover')
async def discover_models(
    connection_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    connection = await connection_access(db, user, connection_id, True)
    if catalog.get_template(connection.template)['discovery'] == 'manual':
        raise HTTPException(422, 'Add models using their deployment names for this template')
    try:
        rows = await catalog.discover(db, connection)
    except Exception:
        # URLs and provider errors can include private information. Keep details out of API/logs.
        raise HTTPException(502, 'Model discovery failed; existing configuration was preserved') from None
    await db.commit()
    return [catalog.binding_json(row, connection) for row in rows]


@router.get('/connections/{connection_id}/browser-config')
async def browser_config(
    connection_id: str,
    revision: int | None = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    from starlette.responses import JSONResponse
    from open_webui.utils.oi.browser_transport import require_browser_owner

    connection = await db.get(Connection, connection_id)
    if connection is None:
        raise HTTPException(404, 'Browser connection unavailable', headers={'Cache-Control': 'no-store'})
    await require_browser_owner(connection, user)
    if revision is not None and revision != connection.revision:
        raise HTTPException(409, 'Connection changed; reload before sending', headers={'Cache-Control': 'no-store'})
    return JSONResponse(
        {
            'id': connection.id,
            'revision': connection.revision,
            'base_url': connection.base_url,
            'protocol': connection.protocol,
            'headers': catalog.request_headers(connection),
        },
        headers={'Cache-Control': 'no-store, private', 'Pragma': 'no-cache', 'Vary': 'Authorization, Cookie'},
    )


class BrowserDiscoveryForm(BaseModel):
    revision: int
    models: list[dict] = Field(max_length=10000)


@router.post('/connections/{connection_id}/browser-models')
async def browser_models(
    connection_id: str,
    form: BrowserDiscoveryForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    from open_webui.utils.oi.browser_transport import require_browser_owner

    connection = (
        await db.execute(select(Connection).where(Connection.id == connection_id).with_for_update())
    ).scalar_one_or_none()
    if connection is None:
        raise HTTPException(404, 'Browser connection unavailable')
    await require_browser_owner(connection, user)
    if connection.revision != form.revision:
        raise HTTPException(409, 'Connection changed; reload before discovering models')
    try:
        rows = await catalog.apply_discovered_models(db, connection, form.models)
    except (ValueError, TypeError):
        await db.rollback()
        raise HTTPException(422, 'Invalid discovered model list; previous configuration preserved') from None
    await db.commit()
    return [catalog.binding_json(row, connection) for row in rows]


class ServerCopyForm(BaseModel):
    revision: int
    request_id: UUID


@router.post('/connections/{connection_id}/server-copy')
async def copy_browser_to_server(
    connection_id: str,
    form: ServerCopyForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    import copy
    from open_webui.models.customization import ConnectionMigration
    from open_webui.utils.oi.storage import identifier, now
    from open_webui.utils.oi.browser_transport import require_browser_owner

    receipt_id = f'copy:{user.id}:{connection_id}:{form.revision}'
    receipt = await db.get(ConnectionMigration, receipt_id)
    if receipt:
        if receipt.result['source_id'] != connection_id:
            raise HTTPException(409, 'Migration request ID was used for another connection')
        return receipt.result
    # Serialize copy retries on SQLite as well as PostgreSQL.
    await db.execute(update(Connection).where(Connection.id == connection_id).values(updated_at=Connection.updated_at))
    source = (
        await db.execute(select(Connection).where(Connection.id == connection_id).with_for_update())
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(404, 'Browser connection unavailable')
    await require_browser_owner(source, user)
    receipt = await db.get(ConnectionMigration, receipt_id, populate_existing=True)
    if receipt:
        return receipt.result
    if source.revision != form.revision:
        raise HTTPException(409, 'Connection changed; reload migration preview')
    target = Connection(
        id=identifier(),
        owner_id=user.id,
        name=source.name + ' · 服务器',
        template=source.template,
        protocol=source.protocol,
        transport='server',
        base_url=source.base_url,
        credentials=source.credentials,
        settings=copy.deepcopy(source.settings),
        shared=False,
        enabled=source.enabled,
        revision=1,
        created_at=now(),
        updated_at=now(),
    )
    try:
        await catalog.fetch_models(target)
    except Exception:
        raise HTTPException(502, 'Server cannot verify this connection; browser connection was preserved') from None
    db.add(target)
    mapping = {}
    for old in (await db.execute(select(ModelBinding).where(ModelBinding.connection_id == source.id))).scalars():
        new = ModelBinding(
            id='oi:' + identifier(),
            connection_id=target.id,
            upstream_id=old.upstream_id,
            protocol=old.protocol,
            name=old.name,
            enabled=old.enabled,
            available=old.available,
            capabilities=copy.deepcopy(old.capabilities),
            pricing=copy.deepcopy(old.pricing),
            params=copy.deepcopy(old.params),
            aliases=[],
            updated_at=now(),
        )
        db.add(new)
        mapping[old.id] = new.id
    result = {'source_id': source.id, 'connection_id': target.id, 'model_mapping': mapping, 'source_preserved': True}
    db.add(ConnectionMigration(id=receipt_id, user_id=user.id, scope='server-copy', result=result, created_at=now()))
    await db.commit()
    return result


@router.get('/model-bindings/history')
async def historical_model_identities(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    from open_webui.utils.oi.history import identities

    return await identities(db, user)


@router.get('/model-bindings/catalog')
async def binding_catalog(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    from open_webui.utils.models import public_model_params

    models = await catalog.model_catalog(db, user)
    for model in models:
        model['info']['params'] = public_model_params(model['info']['params'])
    return models


@router.get('/model-bindings')
async def bindings(
    connection_id: str | None = None, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    query = select(ModelBinding, Connection).join(Connection, ModelBinding.connection_id == Connection.id)
    if connection_id:
        query = query.where(Connection.id == connection_id)
    rows = (await db.execute(query)).all()
    from open_webui.utils.models import public_model_params

    result = []
    for binding, connection in rows:
        if not await catalog.binding_readable(db, binding, connection, user, management=True):
            continue
        item = catalog.binding_json(binding, connection)
        if not catalog.editable(connection, user):
            item['params'] = public_model_params(item['params'])
            item.pop('capability_overrides', None)
        result.append(item)
    return result


class BindingForm(BaseModel):
    connection_id: str
    upstream_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    protocol: str
    enabled: bool = True
    capabilities: dict = Field(default_factory=dict)
    capability_overrides: dict | None = None
    pricing: dict = Field(default_factory=dict)
    params: dict = Field(default_factory=dict)


async def save_binding(db, user, form, binding_id=None):
    connection = await connection_access(db, user, form.connection_id, True)
    if form.protocol not in catalog.PROTOCOLS:
        raise HTTPException(422, 'Unsupported protocol')
    for rate in (form.pricing.get('rates') or {}).values():
        try:
            accounting.money(rate)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
    row = await db.get(ModelBinding, binding_id) if binding_id else None
    if binding_id and (row is None or row.connection_id != connection.id):
        raise HTTPException(404, 'Model binding unavailable')
    overrides = form.capability_overrides if form.capability_overrides is not None else form.capabilities
    effective_caps = {**(row.capabilities or {}).get('discovered', {}), **overrides} if row else overrides
    from types import SimpleNamespace

    try:
        catalog.reasoning_controls(SimpleNamespace(capabilities=effective_caps))
    except ValueError as error:
        raise HTTPException(422, 'Invalid reasoning capability: ' + str(error)) from None
    manual = row is None or (row.capabilities or {}).get('manual', False)
    existing = (
        await db.execute(
            select(ModelBinding).where(
                ModelBinding.connection_id == connection.id,
                ModelBinding.upstream_id == form.upstream_id,
                ModelBinding.protocol == form.protocol,
            )
        )
    ).scalar_one_or_none()
    if existing and existing.id != binding_id:
        raise HTTPException(409, 'This connection already contains the model')
    if row is None:
        row = ModelBinding(id='oi:' + identifier(), connection_id=connection.id, aliases=[], available=True)
        db.add(row)
    for key in ('upstream_id', 'name', 'protocol', 'enabled', 'pricing', 'params'):
        setattr(row, key, getattr(form, key))
    row.capabilities = {
        'discovered': (row.capabilities or {}).get('discovered', {}),
        'overrides': form.capability_overrides if form.capability_overrides is not None else form.capabilities,
        'manual': manual,
    }
    row.updated_at = now()
    await db.commit()
    return catalog.binding_json(row, connection)


@router.post('/model-bindings')
async def create_binding(
    form: BindingForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    return await save_binding(db, user, form)


@router.put('/model-bindings/{binding_id}')
async def update_binding(
    binding_id: str, form: BindingForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    return await save_binding(db, user, form, binding_id)


class NewDraftForm(BaseModel):
    chat_id: UUID = Field(default_factory=uuid4)


@router.post('/chats/drafts')
async def create_draft_chat(
    form: NewDraftForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    chat_id = str(form.chat_id)
    existing = await Chats.get_chat_by_id(chat_id, db=db)
    if existing and existing.user_id != user.id:
        raise HTTPException(409, 'Conversation identity already exists')
    if existing is None:
        await Chats.insert_new_chat(
            chat_id,
            user.id,
            ChatForm(
                chat={
                    'title': 'Draft',
                    'models': [],
                    'history': {'messages': {}, 'currentId': None},
                    'messages': [],
                }
            ),
            db=db,
            internal_meta={'oi_draft': True},
        )
    return await drafts.get_draft(db, user.id, chat_id)


@router.get('/chats/drafts')
async def draft_list(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    return await drafts.list_drafts(db, user.id)


class DraftForm(BaseModel):
    revision: int = Field(ge=0)
    mutation_id: UUID
    payload: dict = Field(default_factory=dict)


class DraftAcknowledgement(BaseModel):
    mutation_id: UUID
    revision: int | None = Field(default=None, ge=0)
    queue_id: UUID | None = None
    request_id: UUID | None = None
    input_version: UUID | None = None


@router.post('/chats/{chat_id}/draft/acknowledge')
async def acknowledge_draft(
    chat_id: str,
    form: DraftAcknowledgement,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await chat_access(db, user, chat_id, True)
    try:
        if form.queue_id is not None and form.request_id is not None:
            result = await drafts.acknowledge_queued(
                db, user.id, chat_id, str(form.queue_id), str(form.request_id), str(form.mutation_id)
            )
        elif form.queue_id is None and form.request_id is None and form.revision is not None:
            result = await drafts.acknowledge_input(
                db,
                user.id,
                chat_id,
                form.revision,
                str(form.mutation_id),
                str(form.input_version) if form.input_version else None,
            )
        else:
            raise HTTPException(422, 'Provide an input revision or a queue/request identity pair')
        await db.commit()
        return result
    except drafts.DraftConflict as error:
        raise HTTPException(409, {'kind': 'draft_conflict', 'current': error.current}) from None


@router.get('/chats/{chat_id}/draft')
async def read_draft(chat_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await chat_access(db, user, chat_id, True)
    return await drafts.get_draft(db, user.id, chat_id)


@router.put('/chats/{chat_id}/draft')
async def write_draft(
    chat_id: str, form: DraftForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    await chat_access(db, user, chat_id, True)
    try:
        result = await drafts.put_draft(db, user.id, chat_id, form.payload, form.revision, str(form.mutation_id))
        await db.commit()
        return result
    except drafts.DraftConflict as error:
        raise HTTPException(409, {'kind': 'draft_conflict', 'current': error.current}) from None


@router.delete('/chats/{chat_id}/draft')
async def delete_draft(
    chat_id: str,
    revision: int,
    mutation_id: UUID,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await write_draft(chat_id, DraftForm(revision=revision, mutation_id=mutation_id), user, db)


def call_json(call):
    return {
        key: getattr(call, key)
        for key in (
            'id',
            'generation_id',
            'model_id',
            'binding_id',
            'connection_id',
            'status',
            'usage',
            'actual_usd',
            'estimated_usd',
            'pricing',
            'source',
            'created_at',
            'completed_at',
        )
    }


@router.get('/chats/{chat_id}/usage')
async def chat_usage(chat_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    if chat_id.startswith('temporary:'):
        ledger = await db.get(ChatLedger, chat_id)
        if ledger is None or ledger.user_id != user.id:
            raise HTTPException(404, 'Conversation billing unavailable')
    else:
        chat = await chat_access(db, user, chat_id)
        if chat.user_id != user.id:
            raise HTTPException(403, 'Only the owner can view conversation billing')
    calls, _ = await accounting.conversation_records(db, chat_id)
    calls = sorted(calls.values(), key=lambda call: (call.created_at, call.sequence, call.id))
    snapshots = await accounting.conversation_snapshots(db, chat_id)
    generations = (
        (
            await db.execute(
                select(Generation).where(
                    (Generation.chat_id == chat_id) | Generation.id.in_({call.generation_id for call in calls})
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        'totals': await accounting.get_totals(db, chat_id),
        'calls': [call_json(call) for call in calls],
        'generations': [
            {k: getattr(g, k) for k in ('id', 'message_id', 'kind', 'status', 'created_at')} for g in generations
        ],
        'snapshots': [
            {
                'id': s.id,
                'message_id': s.message_id,
                'generation_id': s.generation_id,
                **await accounting.get_totals(db, s.chat_id, s.id),
            }
            for s in snapshots
        ],
    }


class BaselineForm(BaseModel):
    batch_id: str = Field(min_length=1, max_length=200)
    actual_usd: str | None = None
    estimated_usd: str | None = None
    note: str = ''


@router.get('/chats/{chat_id}/cost-baselines')
async def baselines(chat_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    chat = await chat_access(db, user, chat_id)
    if chat.user_id != user.id:
        raise HTTPException(403, 'Only the owner can view cost baselines')
    rows = (await db.execute(select(ImportCostBaseline).where(ImportCostBaseline.chat_id == chat_id))).scalars()
    return [
        {k: getattr(row, k) for k in ('id', 'batch_id', 'actual_usd', 'estimated_usd', 'note', 'revision')}
        for row in rows
    ]


@router.put('/chats/{chat_id}/cost-baselines')
async def write_baseline(
    chat_id: str, form: BaselineForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    chat = await chat_access(db, user, chat_id, True)
    if chat.user_id != user.id:
        raise HTTPException(403, 'Only the owner can edit cost baselines')
    try:
        await accounting.set_baseline(db, user.id, chat_id, **form.model_dump())
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    await db.commit()
    return await baselines(chat_id, user, db)


class AnnotationForm(BaseModel):
    revision: int = Field(ge=0)
    data: dict = Field(default_factory=dict)


@router.get('/chats/{chat_id}/annotations')
async def read_annotations(
    chat_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    await chat_access(db, user, chat_id)
    row = await db.get(ChatAnnotation, (user.id, chat_id), populate_existing=True)
    return {'revision': row.revision, 'data': row.data} if row else {'revision': 0, 'data': {}}


@router.put('/chats/{chat_id}/annotations')
async def write_annotations(
    chat_id: str, form: AnnotationForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    await chat_access(db, user, chat_id)
    data = {key: value for key, value in form.data.items() if key in {'bookmarks', 'read_position', 'branch_names'}}
    if form.revision == 0:
        changed = await insert_once(
            db,
            ChatAnnotation,
            dict(user_id=user.id, chat_id=chat_id, revision=1, data=data, updated_at=now()),
            ['user_id', 'chat_id'],
        )
    else:
        result = await db.execute(
            update(ChatAnnotation)
            .where(
                ChatAnnotation.user_id == user.id,
                ChatAnnotation.chat_id == chat_id,
                ChatAnnotation.revision == form.revision,
            )
            .values(data=data, revision=form.revision + 1, updated_at=now())
            .returning(ChatAnnotation.revision)
        )
        changed = result.scalar_one_or_none() is not None
    if not changed:
        raise HTTPException(409, 'Navigation state changed; reload before saving')
    await db.commit()
    return await read_annotations(chat_id, user, db)


@router.get('/chats/{chat_id}/messages/{message_id}/revisions')
async def revisions(
    chat_id: str, message_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    chat = await chat_access(db, user, chat_id)
    if chat.user_id != user.id:
        raise HTTPException(403, 'Only the owner can view revision history')
    rows = (
        await db.execute(
            select(MessageRevision)
            .where(
                MessageRevision.chat_id == chat_id,
                MessageRevision.message_id == message_id,
            )
            .order_by(MessageRevision.created_at.desc())
        )
    ).scalars()
    return [{key: getattr(row, key) for key in ('id', 'content', 'output', 'created_at')} for row in rows]
