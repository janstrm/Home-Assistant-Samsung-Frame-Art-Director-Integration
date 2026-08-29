"""Per-config-entry runtime state for Samsung Frame Art Director."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from .api import SamsungFrameClient


@dataclass(slots=True)
class SamsungFrameRuntimeData:
    """Resources owned by one loaded Samsung Frame config entry."""

    client: SamsungFrameClient
    coordinator: DataUpdateCoordinator[dict[str, Any]] | None = None


type SamsungFrameConfigEntry = ConfigEntry[SamsungFrameRuntimeData]
