"""Switch platform for Samsung Frame Art Director."""
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_SLIDESHOW_ENABLED, DOMAIN, DATA_CLIENT, CONF_DUID, CONF_ENABLE_ART_SETTINGS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    _LOGGER.debug("Setting up switch platform for entry: %s", entry.entry_id)
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
    entities = [
        SamsungFrameSlideshowSwitch(entry),
        SamsungFrameFavoritesSwitch(entry),
    ]
    if entry.options.get(CONF_ENABLE_ART_SETTINGS, False):
        entities.append(SamsungFrameBrightnessSensorSwitch(entry, client))
    async_add_entities(entities, True)


class SamsungFrameSlideshowSwitch(SwitchEntity):
    """Switch entity to enable/disable slideshow."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
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


class SamsungFrameFavoritesSwitch(SwitchEntity):
    """Switch entity to enable/disable filtering by favorites in gallery."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
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


class SamsungFrameBrightnessSensorSwitch(SwitchEntity):
    """Auto-brightness (Art Mode light sensor). Backed by the TV, read once."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Auto Brightness"
    _attr_icon = "mdi:brightness-auto"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, client) -> None:
        self._entry = entry
        self._client = client
        self._is_on: bool | None = None
        self._attr_unique_id = f"{entry.entry_id}_brightness_sensor"
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def is_on(self) -> bool | None:
        return self._is_on

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        val = await self._client.async_get_artmode_setting("brightness_sensor_setting")
        self._is_on = str(val).lower() in ("on", "1", "true") if val is not None else None
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        await self._client.async_set_brightness_sensor(True)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self._client.async_set_brightness_sensor(False)
        self._is_on = False
        self.async_write_ha_state()
