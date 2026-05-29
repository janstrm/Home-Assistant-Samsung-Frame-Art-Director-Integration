"""Media Player platform for Samsung Frame Art Director."""

from __future__ import annotations

import logging

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_DUID, DATA_CLIENT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the Samsung Frame media player from a config entry."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]

    async def async_update_data():
        """Fetch data from API endpoint."""
        status = await client.async_get_artmode_status()
        return {"art_mode_status": status}

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="samsung_frame_art_mode",
        update_method=async_update_data,
        update_interval=dt_util.dt.timedelta(seconds=30),
    )

    await coordinator.async_config_entry_first_refresh()

    async_add_entities([SamsungFrameMediaPlayer(hass, entry, coordinator)])
    # NOTE: the set_artmode / upload_art / rotate_art_now services are registered
    # as domain services in __init__.py (with WoL / power-key / matte / cleanup
    # handling) and target this entity via services.yaml. They are intentionally
    # not registered as entity-platform services here to avoid a duplicate,
    # divergent implementation.


class SamsungFrameMediaPlayer(CoordinatorEntity, MediaPlayerEntity):
    """Representation of the Samsung Frame TV."""

    _attr_has_entity_name = True
    _attr_name = None  # Use device name
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: DataUpdateCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.hass = hass
        self._entry = entry
        self._client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
        self._attr_unique_id = entry.data.get("duid") or entry.entry_id
        # Use duid for device identifiers to ensure all platforms group together
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def state(self):
        """Return the state of the entity based on art mode."""
        if not self._client.is_connected:
            return "off"

        status = self.coordinator.data.get("art_mode_status") if self.coordinator.data else "unknown"
        if status in ("on", "true", "1"):
            return "on"
        elif status in ("off", "false", "0"):
            return "off"
        # If we can't tell, fallback to connected assumption
        return "on" if self._client.is_connected else "off"

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        status = self.coordinator.data.get("art_mode_status") if self.coordinator.data else "unknown"
        return {
            "art_mode_status": str(status).lower() if status is not None else "unknown",
            "connected": self._client.is_connected,
        }

    async def async_turn_on(self) -> None:
        """Turn the media player on (enter Art Mode)."""
        await self._client.async_set_artmode(True)

    async def async_turn_off(self) -> None:
        """Turn the media player off (leave Art Mode)."""
        await self._client.async_set_artmode(False)
