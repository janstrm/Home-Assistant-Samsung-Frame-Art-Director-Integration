"""Resolve Home Assistant action targets to loaded Frame runtimes."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import service as ha_service

from .const import DOMAIN
from .runtime import SamsungFrameConfigEntry, SamsungFrameRuntimeData


@dataclass(frozen=True, slots=True)
class FrameActionTarget:
    """One loaded Frame selected by a Home Assistant action target."""

    entry: SamsungFrameConfigEntry
    runtime: SamsungFrameRuntimeData


def loaded_frame_targets(hass: HomeAssistant) -> list[FrameActionTarget]:
    """Return every config entry that currently owns a loaded Frame runtime."""
    return [
        FrameActionTarget(entry, runtime)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if (runtime := getattr(entry, "runtime_data", None)) is not None
    ]


def loaded_frame_target(
    hass: HomeAssistant, config_entry_id: str
) -> FrameActionTarget | None:
    """Resolve one loaded Frame runtime by config-entry identity."""
    return next(
        (
            target
            for target in loaded_frame_targets(hass)
            if target.entry.entry_id == config_entry_id
        ),
        None,
    )


def entry_entity_id(
    hass: HomeAssistant,
    entry: SamsungFrameConfigEntry,
    entity_domain: str,
    unique_id_suffix: str,
) -> str | None:
    """Resolve an entry-owned entity by stable unique ID.

    Entity IDs are user-editable in Home Assistant, while the integration's
    unique IDs remain stable. Internal action coordination must therefore use
    the entity registry instead of assuming a generated entity ID.
    """
    return er.async_get(hass).async_get_entity_id(
        entity_domain,
        DOMAIN,
        f"{entry.entry_id}_{unique_id_suffix}",
    )


async def async_resolve_action_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[FrameActionTarget]:
    """Resolve an action target or raise a clear validation error."""
    entity_ids = await ha_service.async_extract_entity_ids(call)
    if not entity_ids:
        loaded = loaded_frame_targets(hass)
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
