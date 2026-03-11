"""Switch platform for Samsung Frame Art Director."""
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_SLIDESHOW_ENABLED, DOMAIN, CONF_DUID

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    _LOGGER.debug("Setting up switch platform for entry: %s", entry.entry_id)
    async_add_entities([
        SamsungFrameSlideshowSwitch(entry),
        SamsungFrameMatteSwitch(entry),
        SamsungFrameFavoritesSwitch(entry)
    ], True)


class SamsungFrameSlideshowSwitch(SwitchEntity):
    """Switch entity to enable/disable slideshow."""

    _attr_has_entity_name = True
    _attr_name = "Slideshow Enabled"
    _attr_icon = "mdi:play-pause"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the switch entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_slideshow_enabled"
        # Use duid for device identifiers to ensure all platforms group together
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self._entry.options.get(CONF_SLIDESHOW_ENABLED, False)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        new_data = {**self._entry.options}
        new_data[CONF_SLIDESHOW_ENABLED] = True
        
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        new_data = {**self._entry.options}
        new_data[CONF_SLIDESHOW_ENABLED] = False
        
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()


class SamsungFrameMatteSwitch(SwitchEntity):
    """Switch entity to enable/disable art matte."""

    _attr_has_entity_name = True
    _attr_name = "Matte Enabled"
    _attr_icon = "mdi:picture-in-picture-bottom-right-outline"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the switch entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_matte_enabled"
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        from .const import CONF_MATTE_ENABLED
        return self._entry.options.get(CONF_MATTE_ENABLED, False)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        from .const import CONF_MATTE_ENABLED
        new_data = {**self._entry.options}
        new_data[CONF_MATTE_ENABLED] = True
        
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        from .const import CONF_MATTE_ENABLED
        new_data = {**self._entry.options}
        new_data[CONF_MATTE_ENABLED] = False
        
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()


class SamsungFrameFavoritesSwitch(SwitchEntity):
    """Switch entity to enable/disable filtering by favorites in gallery."""

    _attr_has_entity_name = True
    _attr_name = "Gallery Favorites Only"
    _attr_icon = "mdi:heart-multiple-outline"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the switch entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_favorites_filter"
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self._entry.options.get("favorites_filter_enabled", False)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        new_data = {**self._entry.options}
        new_data["favorites_filter_enabled"] = True
        
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        new_data = {**self._entry.options}
        new_data["favorites_filter_enabled"] = False
        
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()
