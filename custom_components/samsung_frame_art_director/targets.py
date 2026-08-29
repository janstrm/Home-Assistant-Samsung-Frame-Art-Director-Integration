"""Resolve Home Assistant action targets to loaded Frame runtimes."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er, service as ha_service

from .const import DOMAIN
from .runtime import SamsungFrameConfigEntry, SamsungFrameRuntimeData


@dataclass(frozen=True, slots=True)
class FrameActionTarget:
    """One loaded Frame selected by a Home Assistant action target."""

    entry: SamsungFrameConfigEntry
    runtime: SamsungFrameRuntimeData


async def async_resolve_action_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[FrameActionTarget]:
    """Resolve an action target or raise a clear validation error."""
    entity_ids = await ha_service.async_extract_entity_ids(call)
    if not entity_ids:
        loaded = [
            FrameActionTarget(entry, runtime)
            for entry in hass.config_entries.async_entries(DOMAIN)
            if (runtime := getattr(entry, "runtime_data", None)) is not None
        ]
        if len(loaded) == 1:
            return loaded
        if not loaded:
            raise ServiceValidationError(
                "No loaded Samsung Frame Art Director entity is available"
            )
        raise ServiceValidationError(
            "A target is required when multiple Samsung Frames are loaded"
        )

    entity_registry = er.async_get(hass)
    resolved: dict[str, FrameActionTarget] = {}
    invalid: list[str] = []
    for entity_id in entity_ids:
        entity = entity_registry.async_get(entity_id)
        config_entry_id = entity.config_entry_id if entity else None
        entry = (
            hass.config_entries.async_get_entry(config_entry_id)
            if config_entry_id
            else None
        )
        runtime = getattr(entry, "runtime_data", None) if entry else None
        if entry is None or entry.domain != DOMAIN or runtime is None:
            invalid.append(entity_id)
            continue
        resolved[entry.entry_id] = FrameActionTarget(entry, runtime)

    if invalid:
        raise ServiceValidationError(
            "Target is not a loaded Samsung Frame Art Director entity: "
            + ", ".join(sorted(invalid))
        )
    if not resolved:
        raise ServiceValidationError(
            "No loaded Samsung Frame Art Director entity was targeted"
        )
    return list(resolved.values())
