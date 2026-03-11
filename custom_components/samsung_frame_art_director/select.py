"""Select platform for Samsung Frame Art Director."""
import logging
import os
import sqlite3
import voluptuous as vol

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    CONF_SLIDESHOW_SOURCE_TYPE, 
    CONF_SLIDESHOW_INTERVAL,
    CONF_SLIDESHOW_FILTER,
    CONF_SLIDESHOW_SOURCE_PATH,
    DOMAIN, 
    DATA_CLIENT,
    DB_FILE,
    CONF_DUID,
    SLIDESHOW_SOURCE_FOLDER, 
    SLIDESHOW_SOURCE_TAGS, 
    SLIDESHOW_SOURCE_LIBRARY
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
        SamsungFrameSlideshowIntervalSelect(entry),
        SamsungFrameSlideshowContentSelect(hass, entry)
    ], True)


class SamsungFrameSlideshowSourceSelect(SelectEntity):
    """Select entity to choose slideshow source type."""

    _attr_has_entity_name = True
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


class SamsungFrameSlideshowIntervalSelect(SelectEntity):
    """Select entity to choose slideshow interval (minutes)."""

    _attr_has_entity_name = True
    _attr_name = "Slideshow Interval"
    _attr_icon = "mdi:timer-refresh"
    _attr_options = ["1", "2", "5", "10", "15", "30", "60", "120", "240"]

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_slideshow_interval_select"
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
        val = self._entry.options.get(CONF_SLIDESHOW_INTERVAL, 0)
        return str(val) if str(val) in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        new_data = {**self._entry.options}
        new_data[CONF_SLIDESHOW_INTERVAL] = int(option)
        
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()


class SamsungFrameSlideshowContentSelect(SelectEntity):
    """Dynamic select entity to choose content (Folders or Tags) depending on Source Type."""

    _attr_has_entity_name = True
    _attr_name = "Slideshow Content"
    _attr_icon = "mdi:folder-image"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_slideshow_content_select"
        self._attr_options = []
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
        return self._entry.options.get(CONF_SLIDESHOW_FILTER)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        new_data = {**self._entry.options}
        new_data[CONF_SLIDESHOW_FILTER] = option
        
        # Also sync legacy source path if it looks like a path
        if "/" in option and self._entry.options.get(CONF_SLIDESHOW_SOURCE_TYPE) == SLIDESHOW_SOURCE_FOLDER:
             new_data[CONF_SLIDESHOW_SOURCE_PATH] = option

        self.hass.config_entries.async_update_entry(
            self._entry, options=new_data
        )
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update options based on current source type."""
        source_type = self._entry.options.get(CONF_SLIDESHOW_SOURCE_TYPE, SLIDESHOW_SOURCE_FOLDER)
        
        options = []
        
        if source_type == SLIDESHOW_SOURCE_FOLDER:
            # List subfolders of the default media path
            # We assume /media/frame is the root or what's configured
            root_path = "/media/frame/library" 
            
            def _scan_folders():
                try:
                    # Use os.scandir to find directories
                    if os.path.isdir(root_path):
                         paths = [f.path for f in os.scandir(root_path) if f.is_dir()]
                         # Also include the root itself
                         paths.insert(0, root_path)
                         return paths
                except Exception as e:
                    _LOGGER.warning("Could not scan folders: %s", e)
                return [root_path]

            options = await self.hass.async_add_executor_job(_scan_folders)
                
        elif source_type == SLIDESHOW_SOURCE_TAGS:
            # Query DB for tags
            client = self.hass.data[DOMAIN][self._entry.entry_id].get(DATA_CLIENT)
            if client and client._db_path and os.path.exists(client._db_path):
                 try:
                     def _get_tags():
                         with sqlite3.connect(client._db_path) as conn:
                             cursor = conn.execute("SELECT DISTINCT tags FROM art_library WHERE tags IS NOT NULL AND tags != ''")
                             return [row[0] for row in cursor]
                     
                     raw_tags = await self.hass.async_add_executor_job(_get_tags)
                     # Flatten and unique (handle "tag1, tag2")
                     all_tags = set()
                     for t_str in raw_tags:
                         for t in t_str.split(","):
                             clean = t.strip()
                             if clean:
                                 all_tags.add(clean)
                     options = sorted(list(all_tags))
                 except Exception as e:
                     _LOGGER.warning("Could not fetch tags: %s", e)
        
        else:
            # Library or other
            options = ["All"]

        # If current value is not in options, append it so it shows up? 
        # Or let it be (HA might show it as text). 
        # Standard behavior: valid option required.
        current = self._entry.options.get(CONF_SLIDESHOW_FILTER)
        if current and current not in options:
            options.append(current)
            
        self._attr_options = options
