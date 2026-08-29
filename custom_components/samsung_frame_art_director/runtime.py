"""Per-config-entry runtime state for Samsung Frame Art Director."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .api import SamsungFrameClient


@dataclass(slots=True)
class SamsungFrameRuntimeData:
    """Resources owned by one loaded Samsung Frame config entry."""

    client: SamsungFrameClient


type SamsungFrameConfigEntry = ConfigEntry[SamsungFrameRuntimeData]
