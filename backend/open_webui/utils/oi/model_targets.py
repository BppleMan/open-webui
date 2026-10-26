"""Resolve presets to connection-scoped native leaves without changing their display identity."""

from copy import deepcopy
from dataclasses import dataclass

from fastapi import HTTPException
from open_webui.models.models import Models
from open_webui.utils.misc import merge_model_params
from open_webui.utils.oi import catalog
from open_webui.utils.oi.legacy import alias_map


@dataclass
class NativeTarget:
    binding: object
    connection: object
    params: dict
    preset_ids: list[str]


def remap_legacy_reference(reference, aliases, registry):
    if reference not in aliases:
        return reference
    existing = registry.get(reference)
    if existing and existing.get('preset'):
        return reference
    if len(aliases[reference]) != 1 or (existing and not existing.get('oi_binding')):
        raise HTTPException(409, 'Ambiguous legacy model; select a connection-specific model')
    return aliases[reference][0]


async def resolve_native_target(db, reference, user):
    aliases = await alias_map(db, user)
    seen, presets = set(), []
    while reference:
        if reference in seen:
            raise HTTPException(409, 'Model preset contains a cycle')
        seen.add(reference)
        preset = await Models.get_model_by_id(reference, db=db) if not reference.startswith('oi:') else None
        if preset is not None and preset.base_model_id:
            if not preset.is_active:
                raise HTTPException(404, 'Model preset unavailable')
            presets.append(preset)
            reference = preset.base_model_id
            continue
        if reference in aliases:
            if len(aliases[reference]) != 1:
                raise HTTPException(409, 'Ambiguous legacy model; select its connection')
            reference = aliases[reference][0]
        if reference.startswith('oi:'):
            try:
                binding, connection = await catalog.resolve(db, reference, user)
            except (LookupError, ValueError):
                raise HTTPException(404, 'Connection model unavailable') from None
            override = await Models.get_model_by_id(reference, db=db)
            params = deepcopy(binding.params or {})
            if override is not None:
                if not override.is_active:
                    raise HTTPException(404, 'Connection model unavailable')
                params = merge_model_params(params, override.params.model_dump(exclude_unset=True))
            controls = {**params.get('model_controls', {}), **catalog.reasoning_controls(binding)}
            if controls:
                params['model_controls'] = controls
            for preset in reversed(presets):
                params = merge_model_params(params, preset.params.model_dump(exclude_unset=True))
            return NativeTarget(binding, connection, params, [preset.id for preset in presets])
        model = await Models.get_model_by_id(reference, db=db)
        if model is None or not model.base_model_id:
            return None  # Existing non-native providers keep their original dispatcher.
        if not model.is_active:
            raise HTTPException(404, 'Model preset unavailable')
        presets.append(model)
        reference = model.base_model_id
    return None
