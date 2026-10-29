"""Resolve inherited model configuration into a detached per-call snapshot."""

from copy import deepcopy
from types import SimpleNamespace
from open_webui.utils.oi.endpoints import compatibility_profile
from open_webui.utils.oi.protocol_registry import canonical_protocol


def effective_protocol(binding, connection):
    if getattr(binding, '_oi_snapshot', False):
        return canonical_protocol(binding.protocol)
    if (connection.settings or {}).get('routing'):
        return canonical_protocol(getattr(binding, 'protocol_override', None) or connection.protocol)
    return canonical_protocol(binding.protocol)


def snapshot(value):
    fields = value.__table__.columns.keys() if hasattr(value, '__table__') else vars(value).keys()
    return SimpleNamespace(**{key: deepcopy(getattr(value, key)) for key in fields if not key.startswith('_')})


def snapshot_target(binding, connection):
    if getattr(binding, '_oi_snapshot', False):
        return binding, connection
    mode = effective_protocol(binding, connection)
    frozen_binding, frozen_connection = snapshot(binding), snapshot(connection)
    frozen_binding.protocol = 'cpa-agy' if mode == 'gemini' and compatibility_profile(connection) == 'cpa-agy' else mode
    frozen_binding._oi_snapshot = True
    return frozen_binding, frozen_connection
