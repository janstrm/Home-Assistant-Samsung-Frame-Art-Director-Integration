"""Media Player platform for Samsung Frame Art Director."""

from __future__ import annotations

import logging

from homeassistant.components import media_source
from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_DUID, DATA_CLIENT, DOMAIN, resolve_matte

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the Samsung Frame media player from a config entry."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]

    async def async_update_data():
        """Fetch art-mode status + current artwork over a single connection."""
        return await client.async_get_state()

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
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
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

        status = (self.coordinator.data or {}).get("status")
        if status in ("on", "true", "1"):
            return "on"
        elif status in ("off", "false", "0"):
            return "off"
        # If we can't tell, fallback to connected assumption
        return "on" if self._client.is_connected else "off"

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        data = self.coordinator.data or {}
        status = data.get("status")
        return {
            "art_mode_status": status if status is not None else "unknown",
            "connected": self._client.is_connected,
            "content_id": data.get("content_id"),
        }

    async def async_turn_on(self) -> None:
        """Turn the media player on (enter Art Mode)."""
        await self._client.async_set_artmode(True)

    async def async_turn_off(self) -> None:
        """Turn the media player off (leave Art Mode)."""
        await self._client.async_set_artmode(False)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Browse media sources, limited to images (the art library)."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("image"),
        )

    async def async_play_media(self, media_type: str, media_id: str, **kwargs) -> None:
        """Upload and display a library image selected from the Media panel."""
        if not media_source.is_media_source_id(media_id):
            raise HomeAssistantError("Only Home Assistant media-source items are supported")
        sourced = MediaSourceItem.from_uri(self.hass, media_id, None)
        if sourced.domain != DOMAIN:
            raise HomeAssistantError("Only Samsung Frame Art library items can be sent to the Frame")

        from urllib.parse import unquote

        path = unquote(sourced.identifier)

        def _read() -> bytes:
            with open(path, "rb") as f:
                return f.read()

        image_bytes = await self.hass.async_add_executor_job(_read)
        await self._client.async_upload_image(
            image_bytes, matte=resolve_matte(self._entry.options), source_file=path
        )
