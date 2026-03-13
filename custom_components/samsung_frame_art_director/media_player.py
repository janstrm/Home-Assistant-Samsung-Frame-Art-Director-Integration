"""Media Player platform for Samsung Frame Art Director."""

from __future__ import annotations

import logging
import os
import io

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import DeviceInfo

import voluptuous as vol

from .const import CONF_DUID, DATA_CLIENT, DOMAIN, DB_DIR, DB_FILE, DEFAULT_CLEANUP_MAX_ITEMS

_LOGGER = logging.getLogger(__name__)


from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util import dt as dt_util

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

    entity = SamsungFrameMediaPlayer(hass, entry, coordinator)
    async_add_entities([entity])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_artmode",
        {vol.Required("enabled"): bool},
        "async_set_artmode_service",
    )
    platform.async_register_entity_service(
        "upload_art",
        {
            vol.Required("path"): str,
            vol.Optional("matte"): str,
            vol.Optional("tags"): str,
        },
        "async_upload_art_service",
    )
    platform.async_register_entity_service(
        "rotate_art_now",
        {
            vol.Optional("tags"): str,
            vol.Optional("match_all"): bool,
            vol.Optional("source"): vol.In(["library", "folder"]),
            vol.Optional("path"): str,
        },
        "async_rotate_art_service",
    )

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
            "connected": self._client.is_connected
        }

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        await self._client.async_set_artmode(True)

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        await self._client.async_set_artmode(False)

    async def async_set_artmode_service(self, enabled: bool) -> None:
        """Service to set art mode."""
        await self._client.async_set_artmode(enabled)

    async def async_upload_art_service(self, path: str, matte: str | None = None, tags: str | None = None) -> None:
        """Upload art service."""
        def _read() -> bytes:
            import os
            norm = os.path.expanduser(path)
            if not norm.startswith("/media/") and not norm.startswith("/config/"):
                norm = "/media/frame/library/" + norm.lstrip("/")
            
            abs_norm = os.path.abspath(norm)
            allowed_media = os.path.abspath("/media")
            allowed_config = os.path.abspath(self.hass.config.path())
            if not abs_norm.startswith(allowed_media) and not abs_norm.startswith(allowed_config):
                raise ValueError(f"Path traversal detected or unallowed path: {abs_norm}")
            
            with open(abs_norm, "rb") as f:
                return f.read()

        image_bytes = await self.hass.async_add_executor_job(_read)
        
        # Upload
        await self._client.async_upload_image(image_bytes, matte=matte)
        
        from os.path import basename
        remote_filename = basename(path) 
        
        # Track and cleanup
        await self._client.async_track_art(remote_filename, tags=tags)
        
        cleanup_max = self._entry.options.get("cleanup_max_items", DEFAULT_CLEANUP_MAX_ITEMS)
        await self._client.async_cleanup_storage(max_items=cleanup_max)

    async def async_rotate_art_service(self, tags: str | None = None, match_all: bool = False, source: str = "library", path: str | None = None) -> None:
        """Rotate art using optional tag filters or folder source."""
        _LOGGER.debug("rotate_art_now service called for %s with tags=%s match_all=%s source=%s", self.entity_id, tags, match_all, source)
        
        if source == "folder":
             # Use provided path or default from options (if accessible)
             if not path:
                 # Best effort default
                 path = "/media/frame/library"
             success = await self._client.async_rotate_from_folder(path)
             if success:
                 _LOGGER.info("Rotate art (folder) success on %s", self.entity_id)
             else:
                 _LOGGER.warning("Rotate art (folder) failed on %s", self.entity_id)
        else:
            tag_list = [t.strip() for t in tags.split(",")] if tags else None
            success = await self._client.async_rotate_art(tags=tag_list, match_all=match_all)
            
            if success:
                _LOGGER.info("Rotate art success on %s", self.entity_id)
            else:
                _LOGGER.warning("Rotate art found no matches for tags=%s", tags)

