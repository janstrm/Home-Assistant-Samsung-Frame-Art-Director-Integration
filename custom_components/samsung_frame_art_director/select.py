"""Select platform for Samsung Frame Art Director."""
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    CONF_SLIDESHOW_SOURCE_TYPE,
    DOMAIN,
    CONF_DUID,
    SLIDESHOW_SOURCE_FOLDER,
    SLIDESHOW_SOURCE_TAGS,
    SLIDESHOW_SOURCE_LIBRARY,
    CONF_MATTE_STYLE,
    CONF_MATTE_COLOR,
    CONF_MATTE_ENABLED,
    MATTE_STYLES,
    MATTE_COLORS,
    MATTE_STYLE_NONE,
    DEFAULT_MATTE_STYLE,
    DEFAULT_MATTE_COLOR,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    _LOGGER.debug("Setting up select platform for entry: %s", entry.entry_id)
    async_add_entities([
        SamsungFrameSlideshowSourceSelect(entry),
        SamsungFrameMatteStyleSelect(entry),
        SamsungFrameMatteColorSelect(entry),
    ], True)


class SamsungFrameSlideshowSourceSelect(SelectEntity):
    """Select entity to choose slideshow source type."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Slideshow Source"
    _attr_icon = "mdi:source-branch"
    _attr_options = [SLIDESHOW_SOURCE_FOLDER, SLIDESHOW_SOURCE_TAGS, SLIDESHOW_SOURCE_LIBRARY]

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_slideshow_source"
        # Use duid for device identifiers to ensure all platforms group together
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option."""
        return self._entry.options.get(CONF_SLIDESHOW_SOURCE_TYPE, SLIDESHOW_SOURCE_FOLDER)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        new_data = {**self._entry.options}
        new_data[CONF_SLIDESHOW_SOURCE_TYPE] = option
        
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()


class SamsungFrameMatteStyleSelect(SelectEntity):
    """Select the matte (border) style. 'none' disables the matte."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Matte Style"
    _attr_icon = "mdi:image-frame"
    _attr_options = MATTE_STYLES

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_matte_style"
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected matte style."""
        style = self._entry.options.get(CONF_MATTE_STYLE)
        if style is None:
            # Legacy installs only had the matte_enabled on/off switch.
            if self._entry.options.get(CONF_MATTE_ENABLED):
                return DEFAULT_MATTE_STYLE
            return MATTE_STYLE_NONE
        return style if style in self._attr_options else MATTE_STYLE_NONE

    async def async_select_option(self, option: str) -> None:
        """Change the selected matte style."""
        new_data = {**self._entry.options}
        new_data[CONF_MATTE_STYLE] = option
        self.hass.config_entries.async_update_entry(self._entry, options=new_data)
        self.async_write_ha_state()


class SamsungFrameMatteColorSelect(SelectEntity):
    """Select the matte (border) color. Ignored when the style is 'none'."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Matte Color"
    _attr_icon = "mdi:palette"
    _attr_options = MATTE_COLORS

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_matte_color"
        device_id = entry.data.get(CONF_DUID) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected matte color."""
        return self._entry.options.get(CONF_MATTE_COLOR, DEFAULT_MATTE_COLOR)

    async def async_select_option(self, option: str) -> None:
        """Change the selected matte color."""
        new_data = {**self._entry.options}
        new_data[CONF_MATTE_COLOR] = option
        self.hass.config_entries.async_update_entry(self._entry, options=new_data)
        self.async_write_ha_state()
