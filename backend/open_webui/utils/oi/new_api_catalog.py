"""Join New API's public catalog to models already visible to the connection's credential."""

import re
from urllib.parse import urlsplit, urlunsplit


def pricing_url(models_url):
    url = urlsplit(models_url)
    for suffix in ('/v1beta/openai/models', '/v1beta/models', '/v1/models'):
        if url.path.endswith(suffix):
            return urlunsplit((url.scheme, url.netloc, url.path[: -len(suffix)] + '/api/pricing', '', ''))
    return None


def is_new_api_page(body, models):
    return body.get('success') is True and any(isinstance(m.get('supported_endpoint_types'), list) for m in models)


CHAT_PATHS = {'/v1/chat/completions', '/v1/responses', '/v1/messages', '/v1beta/models/{model}:generateContent'}


def catalog_rows(body):
    if not isinstance(body, dict) or body.get('success') is False or not isinstance(body.get('data'), list):
        raise ValueError('Invalid New API pricing catalog')
    # Some installations return multiple rows per model. Only their common descriptive
    # fields are model metadata. Channel billing expressions never belong in the model catalog.
    endpoints = body.get('supported_endpoint')
    endpoints = endpoints if isinstance(endpoints, dict) else {}
    records = {}
    for row in body['data']:
        if not isinstance(row, dict) or not isinstance(row.get('model_name'), str):
            raise ValueError('Invalid New API pricing record')
        name = row['model_name']
        data = {k: row[k] for k in ('description', 'tags') if isinstance(row.get(k), str)}
        types = row.get('supported_endpoint_types')
        if isinstance(types, list) and all(isinstance(t, str) for t in types):
            data['endpoint_types'] = sorted(set(types))
            if any(
                isinstance(route := endpoints.get(t), dict)
                and route.get('method') == 'POST'
                and isinstance(route.get('path'), str)
                and route['path'] in CHAT_PATHS
                for t in types
            ):
                data['chat'] = True
        if name in records:
            records[name] = {k: v for k, v in records[name].items() if data.get(k) == v}
        else:
            records[name] = data
    return records


def attach_records(models, protocol, records):
    for model in models:
        name = model.get('id') or model.get('name')
        if isinstance(name, str):
            if protocol.startswith('gemini'):
                name = name.removeprefix('models/')
            model['_oi_catalog'] = records.get(name, {}) if records is not None else None


def metadata(record):
    description = record.get('description')
    tags = record.get('tags')
    return {
        **({'chat': True} if record.get('chat') is True else {}),
        **({'endpoint_types': record['endpoint_types']} if isinstance(record.get('endpoint_types'), list) else {}),
        'description': description.strip() if isinstance(description, str) else '',
        'tags': list(dict.fromkeys(t.strip() for t in re.split('[,，]', tags) if t.strip()))
        if isinstance(tags, str)
        else [],
    }


def declared_capabilities(meta):
    tags = meta.get('tags', [])
    declarations = {
        '工具': 'tools',
        'tool calling': 'tools',
        'function calling': 'tools',
        'tools': 'tools',
        '视觉': 'vision',
        '图像输入': 'vision',
        'vision': 'vision',
        '聊天': 'chat',
        'chat': 'chat',
    }
    result = {declarations[t.lower()]: True for t in tags if t.lower() in declarations}
    if meta.get('chat') is True:
        result['chat'] = True
    # K/M are display labels. Do not feed rounded values to compaction or request limits.
    labels = {t.upper() for t in tags if re.fullmatch(r'[1-9][0-9]*(?:\.[0-9]+)?[KM]', t, re.IGNORECASE)}
    if len(labels) == 1:
        result['context_window_label'] = labels.pop()
    return result
