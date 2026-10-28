"""Protocol-specific service roots, shared by discovery and generation."""

from urllib.parse import urlsplit, urlunsplit


def api_base(connection, protocol=None):
    base = connection.base_url.rstrip('/')
    if (protocol or connection.protocol) != 'cpa-agy':
        return base
    # CPA exposes native Gemini under /v1beta, while its compatible discovery
    # endpoint also accepts /v1/models. Accept either service address without
    # making a failed model request first. Keep any reverse-proxy path prefix.
    url = urlsplit(base)
    path = url.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3]
    if not path.endswith('/v1beta'):
        path += '/v1beta'
    return urlunsplit((url.scheme, url.netloc, path, url.query, url.fragment))
