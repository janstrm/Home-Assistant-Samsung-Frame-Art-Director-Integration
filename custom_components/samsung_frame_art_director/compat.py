"""Compatibility helpers for supported Home Assistant releases."""

from __future__ import annotations

from inspect import signature
from logging import Logger
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import service as ha_service
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


async def async_extract_entity_ids(
    hass: HomeAssistant, call: ServiceCall
) -> set[str]:
    """Extract action targets across the supported HA service-helper APIs."""
    extractor = ha_service.async_extract_entity_ids
    if "hass" in signature(extractor).parameters:
        return await extractor(hass, call)
    return await extractor(call)


def create_entry_coordinator(
    hass: HomeAssistant,
    logger: Logger,
    entry: ConfigEntry,
    **kwargs: Any,
) -> DataUpdateCoordinator:
    """Create a coordinator with config-entry ownership when HA supports it."""
    if "config_entry" in signature(DataUpdateCoordinator).parameters:
        kwargs["config_entry"] = entry
    return DataUpdateCoordinator(hass, logger, **kwargs)
