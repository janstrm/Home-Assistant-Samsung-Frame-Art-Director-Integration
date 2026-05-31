"""Number platform for Samsung Frame Art Director."""
import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_SLIDESHOW_INTERVAL, DEFAULT_SLIDESHOW_INTERVAL, DOMAIN, CONF_DUID, DATA_CLIENT, CONF_ENABLE_ART_SETTINGS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    _LOGGER.debug("Setting up number platform for entry: %s", entry.entry_id)
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
    entities = [
        SamsungFrameSlideshowInterval(entry),
        SamsungFrameGalleryPage(entry),
    ]
    if entry.options.get(CONF_ENABLE_ART_SETTINGS, True):
        entities += [
            SamsungFrameBrightness(entry, client),
            SamsungFrameColorTemperature(entry, client),
            SamsungFrameMotionSensitivity(entry, client),
        ]
    async_add_entities(entities, True)


class SamsungFrameSlideshowInterval(NumberEntity):
    """Number entity to control slideshow interval (minutes)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
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
        """Return the current value (preconfigured default when unset)."""
        return self._entry.options.get(CONF_SLIDESHOW_INTERVAL) or DEFAULT_SLIDESHOW_INTERVAL

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
    _attr_entity_category = EntityCategory.CONFIG
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


class _SamsungFrameArtSetting(NumberEntity):
    """Base for Art-Mode display settings read from / written to the TV.

    These don't poll (to avoid extra TV connections); the value is read once
    when added and updated optimistically on change. Changes made with the TV
    remote won't be reflected until HA restarts.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_native_step = 1

    def __init__(self, entry: ConfigEntry, client, suffix: str) -> None:
        self._entry = entry
        self._client = client
        self._value: float | None = None
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def native_value(self) -> float | None:
        return self._value


class SamsungFrameBrightness(_SamsungFrameArtSetting):
    """Art Mode brightness (0-10)."""

    _attr_name = "Art Mode Brightness"
    _attr_icon = "mdi:brightness-6"
    _attr_native_min_value = 0
    _attr_native_max_value = 10

    def __init__(self, entry: ConfigEntry, client) -> None:
        super().__init__(entry, client, "art_brightness")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._value = await self._client.async_get_brightness()
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        await self._client.async_set_brightness(int(value))
        self._value = int(value)
        self.async_write_ha_state()


class SamsungFrameColorTemperature(_SamsungFrameArtSetting):
    """Art Mode color temperature (-5..5)."""

    _attr_name = "Art Mode Color Temperature"
    _attr_icon = "mdi:thermometer"
    _attr_native_min_value = -5
    _attr_native_max_value = 5

    def __init__(self, entry: ConfigEntry, client) -> None:
        super().__init__(entry, client, "art_color_temp")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._value = await self._client.async_get_color_temperature()
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        await self._client.async_set_color_temperature(int(value))
        self._value = int(value)
        self.async_write_ha_state()


class SamsungFrameMotionSensitivity(_SamsungFrameArtSetting):
    """Art Mode motion-sensor sensitivity (1-3)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Motion Sensitivity"
    _attr_icon = "mdi:motion-sensor"
    _attr_native_min_value = 1
    _attr_native_max_value = 3

    def __init__(self, entry: ConfigEntry, client) -> None:
        super().__init__(entry, client, "motion_sensitivity")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        val = await self._client.async_get_artmode_setting("motion_sensitivity")
        try:
            self._value = int(val) if val is not None else None
        except (ValueError, TypeError):
            self._value = None
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        await self._client.async_set_motion_sensitivity(int(value))
        self._value = int(value)
        self.async_write_ha_state()
