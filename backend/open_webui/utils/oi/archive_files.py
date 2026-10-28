"""Bounded ZIP transport for archives; never fetch URLs supplied by an archive."""

import asyncio
import copy
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path
from sqlalchemy import select

from open_webui.models.files import File as StoredFile
from open_webui.storage.provider import Storage
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.oi import archive
from open_webui.utils.oi.storage import identifier, now

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_MANIFEST = 64 * 1024 * 1024
FILE_URL = re.compile(r'/api/v1/files/([^/?#]+)(?:/|$)')


def attachment_references(value):
    ids, external = set(), set()

    def visit(item, attachment=False):
        if isinstance(item, str):
            ids.update(re.findall(r'/api/v1/files/([A-Za-z0-9:_-]+)/', item))
        elif isinstance(item, list):
            for child in item:
                visit(child, attachment)
        elif isinstance(item, dict):
            if attachment and item.get('type') in {'filesystem', 'note', 'chat', 'folder', 'collection'}:
                external.add(f'{item["type"]}:{item.get("id") or item.get("path") or item.get("name") or "reference"}')
            if (
                attachment
                and item.get('type') in {'file', 'image', 'doc', 'audio', 'video'}
                and isinstance(item.get('id'), str)
            ):
                ids.add(item['id'])
            if isinstance(item.get('file_id'), str):
                ids.add(item['file_id'])
            if isinstance(item.get('file'), dict) and isinstance(item['file'].get('id'), str):
                ids.add(item['file']['id'])
            for key, child in item.items():
                if key in {'url', 'image_url'} and isinstance(child, str):
                    match = FILE_URL.search(child)
                    if match:
                        ids.add(match[1])
                    elif attachment and not child.startswith('data:'):
                        if child.startswith(('http://', 'https://', '/')):
                            external.add(child)
                        elif item.get('type') == 'image':
                            ids.add(child)
                visit(child, attachment or key in {'files', 'file', 'image_url', 'input_image'})

    visit(value)
    return ids, external


async def create_bundle(db, user, manifest):
    manifest = copy.deepcopy(manifest)
    ids, external = attachment_references(manifest['chat'])
    for revision in manifest.get('revisions', []):
        more, links = attachment_references(revision)
        ids.update(more)
        external.update(links)
    assets = []
    sources = {}
    size = 0
    for file_id in sorted(ids):
        stored = await db.get(StoredFile, file_id)
        permitted = stored is not None and (
            stored.user_id == user.id or user.role == 'admin' or await has_access_to_file(file_id, 'read', user, db=db)
        )
        if not permitted or not stored.path:
            assets.append({'id': file_id, 'status': 'unavailable'})
            continue
        try:
            local = await asyncio.to_thread(Storage.get_file, stored.path)
            path = Path(local)
            size += path.stat().st_size
            if size > MAX_ARCHIVE:
                raise ValueError('Archive attachments exceed 512 MiB')

            def digest():
                with path.open('rb') as source:
                    return hashlib.file_digest(source, 'sha256').hexdigest()

            content_hash = await asyncio.to_thread(digest)
            blob = f'files/{content_hash}'
            sources[blob] = str(path)
            assets.append(
                {
                    'id': file_id,
                    'status': 'included',
                    'filename': stored.filename,
                    'sha256': content_hash,
                    'size': path.stat().st_size,
                    'blob': blob,
                    'data': copy.deepcopy(stored.data or {}),
                    'meta': {
                        key: copy.deepcopy(value)
                        for key, value in (stored.meta or {}).items()
                        if key in {'name', 'content_type', 'size'}
                    },
                }
            )
        except (FileNotFoundError, OSError):
            assets.append({'id': file_id, 'status': 'unavailable'})
    manifest['attachments'] = assets
    manifest['external_references'] = sorted(external)
    manifest['attachment_indexes'] = 'original_file_bytes_and_extracted_text; vector_indexes_not_included'
    encoded = json.dumps(manifest, ensure_ascii=False).encode()
    if len(encoded) > MAX_MANIFEST:
        raise ValueError('Archive manifest exceeds 64 MiB')
    fd, path = tempfile.mkstemp(prefix='oi-archive-', suffix='.zip')
    os.close(fd)
    try:

        def write():
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr('manifest.json', encoded)
                written = 0
                for name, source in sources.items():
                    checksum = hashlib.sha256()
                    with open(source, 'rb') as reader, bundle.open(name, 'w') as writer:
                        while chunk := reader.read(1024 * 1024):
                            written += len(chunk)
                            if written > MAX_ARCHIVE:
                                raise ValueError('Attachments changed beyond the archive size limit')
                            checksum.update(chunk)
                            writer.write(chunk)
                    if checksum.hexdigest() != name.split('/')[-1]:
                        raise ValueError('An attachment changed during export; retry the archive')

        await asyncio.to_thread(write)
        return path, manifest
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def read_bundle(stream):
    stream.seek(0)
    bundle = zipfile.ZipFile(stream)
    try:
        return _read_bundle(bundle)
    except BaseException:
        bundle.close()
        raise


def _read_bundle(bundle):
    entries = bundle.infolist()
    if len(entries) > 10001 or sum(entry.file_size for entry in entries) > MAX_ARCHIVE + MAX_MANIFEST:
        raise ValueError('Archive exceeds the supported size')
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)) or 'manifest.json' not in names:
        raise ValueError('Missing or duplicate archive manifest')
    if any(stat.S_ISLNK(entry.external_attr >> 16) for entry in entries):
        raise ValueError('Archive symlinks are not supported')
    if bundle.getinfo('manifest.json').file_size > MAX_MANIFEST:
        raise ValueError('Archive manifest is too large')
    manifest = json.loads(bundle.read('manifest.json'))
    archive.validate(manifest)
    assets = manifest.get('attachments', [])
    if not isinstance(assets, list) or len(assets) > 10000:
        raise ValueError('Invalid archive attachments')
    seen = set()
    allowed = {'manifest.json'}
    for asset in assets:
        if (
            not isinstance(asset, dict)
            or not isinstance(asset.get('id'), str)
            or not asset['id']
            or asset['id'] in seen
        ):
            raise ValueError('Invalid or duplicated attachment identity')
        seen.add(asset['id'])
        if asset.get('status') not in {'included', 'unavailable'}:
            raise ValueError('Invalid attachment status')
        if asset.get('status') != 'included':
            continue
        name = asset.get('blob', '')
        if not re.fullmatch(r'files/[a-f0-9]{64}', name) or name not in names:
            raise ValueError('Invalid attachment location')
        if bundle.getinfo(name).file_size != asset.get('size'):
            raise ValueError('Attachment size differs from manifest')
        allowed.add(name)
        if not isinstance(asset.get('data', {}), dict) or not isinstance(asset.get('meta', {}), dict):
            raise ValueError('Invalid attachment metadata')
        data = bundle.read(name)
        if hashlib.sha256(data).hexdigest() != asset.get('sha256'):
            raise ValueError('Attachment checksum differs from manifest')
    if set(names) != allowed or any(
        entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG} for entry in entries
    ):
        raise ValueError('Unlisted or unsafe archive entry')
    return manifest, bundle


def rewrite_references(value, mapping, attachment=False):
    if isinstance(value, list):
        return [rewrite_references(item, mapping, attachment) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == 'oi_native':
            result[key] = copy.deepcopy(item)
            continue
        if isinstance(item, str):
            if key in {'file_id'} or (attachment and key in {'id', 'url'}):
                item = mapping.get(item, item)
            if key in {'url', 'content', 'text', 'image_url'}:
                for old, new in mapping.items():
                    item = re.sub(
                        r'(?:https?://[^/\s)]+)?/api/v1/files/' + re.escape(old) + r'/', f'/api/v1/files/{new}/', item
                    )
        result[key] = rewrite_references(item, mapping, attachment or key in {'files', 'file', 'image_url'})
        if attachment and key == 'collection_name':
            result.pop(key, None)
    return result


async def restore_files(db, user_id, manifest, bundle):
    mapping = {}
    created = []
    try:
        for asset in manifest.get('attachments', []):
            if asset.get('status') != 'included':
                continue
            file_id = archive.canonical_id(user_id, 'file', asset['id'] + ':' + asset['sha256'])
            mapping[asset['id']] = file_id
            existing = (
                await db.execute(select(StoredFile).where(StoredFile.id == file_id).with_for_update())
            ).scalar_one_or_none()
            if existing:
                if existing.user_id != user_id or existing.hash != asset['sha256']:
                    raise ValueError('Imported file identity unavailable')

                def valid_blob():
                    try:
                        with Path(Storage.get_file(existing.path)).open('rb') as source:
                            return hashlib.file_digest(source, 'sha256').hexdigest() == asset['sha256']
                    except (FileNotFoundError, OSError):
                        return False

                if existing.path and await asyncio.to_thread(valid_blob):
                    continue
            data = bundle.read(asset['blob'])
            filename = Path(asset.get('filename') or 'attachment').name[:200]
            _, path = await asyncio.to_thread(
                Storage.upload_file, io.BytesIO(data), f'{file_id}_{identifier()}_{filename}', {'user_id': user_id}
            )
            created.append(path)
            if existing:
                existing.path = path
                existing.updated_at = now() // 1000
                continue
            extracted = asset.get('data') or {}
            # Keep extracted text, never a source server's vector collection or permissions.
            extracted = {
                key: copy.deepcopy(value)
                for key, value in extracted.items()
                if key not in {'collection_name', 'access_grants', 'user_id'}
            }
            db.add(
                StoredFile(
                    id=file_id,
                    user_id=user_id,
                    filename=filename,
                    path=path,
                    hash=asset['sha256'],
                    data=extracted,
                    meta={
                        key: value
                        for key, value in (asset.get('meta') or {}).items()
                        if key in {'name', 'size', 'content_type'}
                    },
                    created_at=now() // 1000,
                    updated_at=now() // 1000,
                )
            )
        await db.flush()
        restored = copy.deepcopy(manifest)
        restored['chat'] = rewrite_references(restored['chat'], mapping)
        restored['revisions'] = rewrite_references(restored.get('revisions', []), mapping)
        for mid, message in restored['chat'].get('history', {}).get('messages', {}).items():
            original = manifest['chat']['history']['messages'][mid]
            if message != original and message.get('oi_native'):
                message['oi_native_invalidated'] = True
        return restored, created
    except BaseException:
        await cleanup(created)
        raise


async def cleanup(paths):
    for path in paths:
        await asyncio.to_thread(Storage.delete_file, path)
