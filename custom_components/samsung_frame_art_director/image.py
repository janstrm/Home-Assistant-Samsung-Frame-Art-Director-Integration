"""Image platform for Samsung Frame Art Director."""
from __future__ import annotations

import logging
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DATA_CLIENT, CONF_DUID

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the image platform."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
    async_add_entities([SamsungFrameArtImage(hass, entry, client)], True)

class SamsungFrameArtImage(ImageEntity):
    """Representation of the currently displayed TV art."""

    _attr_has_entity_name = True
    _attr_name = "Art Preview"
    _attr_icon = "mdi:image-frame"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client) -> None:
        """Initialize the image entity."""
        super().__init__(hass)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_art_preview"
        
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )
        self._last_image: bytes | None = None
        self._last_content_id: str | None = None

    async def async_image(self) -> bytes | None:
        """Return bytes of image."""
        _LOGGER.debug("Art Preview: fetching fresh image data")
        art_data = await self._client.async_get_current_art()
        
        content_id = art_data.get("content_id")
        image_bytes = art_data.get("image")
        
        if image_bytes:
            _LOGGER.debug("Art Preview: received %d bytes for ID %s", len(image_bytes), content_id)
        else:
            _LOGGER.debug("Art Preview: no image bytes received for ID %s", content_id)

        if content_id != self._last_content_id:
            _LOGGER.debug("Art Preview: content_id changed from %s to %s", self._last_content_id, content_id)
            self._last_content_id = content_id
            self._attr_image_last_updated = dt_util.utcnow()

        self._last_image = image_bytes
        return image_bytes
