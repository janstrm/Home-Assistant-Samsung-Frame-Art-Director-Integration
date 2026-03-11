"""Text platform for Samsung Frame Art Director."""
import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_SLIDESHOW_FILTER, DOMAIN, CONF_SLIDESHOW_SOURCE_PATH, CONF_DUID

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the text platform."""
    _LOGGER.debug("Setting up text platform for entry: %s", entry.entry_id)
    async_add_entities([SamsungFrameSlideshowFilterText(entry)], True)


class SamsungFrameSlideshowFilterText(TextEntity):
    """Text entity to filter slideshow (Folder filters or Tag list)."""

    _attr_has_entity_name = True
    _attr_name = "Slideshow Filter"
    _attr_icon = "mdi:filter-variant"
    _attr_native_min = 0
    _attr_native_max = 255

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the text entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_slideshow_filter"
        # Use duid for device identifiers to ensure all platforms group together
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def native_value(self) -> str:
        """Return the value reported by the text entity."""
        # Fallback for folder: use the old key if new filter is empty, for backward compatibility
        val = self._entry.options.get(CONF_SLIDESHOW_FILTER)
        if val is None:
            val = ""
        return val

    async def async_set_value(self, value: str) -> None:
        """Change the value."""
        new_data = {**self._entry.options}
        new_data[CONF_SLIDESHOW_FILTER] = value
        
        # Also sync the legacy folder path key if it looks like a path, just in case
        if value.startswith("/") or ":" in value: 
             new_data[CONF_SLIDESHOW_SOURCE_PATH] = value

        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()
