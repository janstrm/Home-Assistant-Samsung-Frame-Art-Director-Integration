"""Number platform for Samsung Frame Art Director."""
import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_SLIDESHOW_INTERVAL, DOMAIN, CONF_DUID

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    _LOGGER.debug("Setting up number platform for entry: %s", entry.entry_id)
    async_add_entities([SamsungFrameSlideshowInterval(entry), SamsungFrameGalleryPage(entry)], True)


class SamsungFrameSlideshowInterval(NumberEntity):
    """Number entity to control slideshow interval (minutes)."""

    _attr_has_entity_name = True
    _attr_name = "Slideshow Interval"
    _attr_native_min_value = 0
    _attr_native_max_value = 1440  # 24 hours
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:timer-refresh"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the number entity."""
        self._entry = entry
        # Unique ID based on entry ID
        self._attr_unique_id = f"{entry.entry_id}_slideshow_interval"
        # Use duid for device identifiers to ensure all platforms group together
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self._entry.options.get(CONF_SLIDESHOW_INTERVAL, 0)

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        new_data = {**self._entry.options}
        new_data[CONF_SLIDESHOW_INTERVAL] = int(value)
        
        # This will trigger the update listener in __init__.py
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()


class SamsungFrameGalleryPage(NumberEntity):
    """Number entity to control gallery page (ephemeral state)."""

    _attr_has_entity_name = True
    _attr_name = "Gallery Page"
    _attr_native_min_value = 1
    _attr_native_max_value = 1000
    _attr_native_step = 1
    _attr_icon = "mdi:book-open-page-variant"
    _attr_mode = "box"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the number entity."""
        self._entry = entry
        # Unique ID based on entry ID
        self._attr_unique_id = f"{entry.entry_id}_gallery_page"
        # Use duid for device identifiers
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )
        # Store state in memory only, defaulting to 1
        self._current_page = 1.0

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self._current_page

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        self._current_page = value
        self.async_write_ha_state()
