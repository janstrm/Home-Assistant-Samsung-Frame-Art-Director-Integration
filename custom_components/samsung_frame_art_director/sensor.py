from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DATA_CLIENT

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Samsung Frame Sensor platform."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
    
    # Create a simple coordinator to fetch library data periodically
    # or on demand via event listeners if we get fancy later.
    # For now, 1 minute refresh is fine, or just on load.
    
    async def _fetch_library():
        # Get all items from DB (still relatively small for memory, big for HA attributes)
        data = await client.async_get_library_data()
        all_items = data.get("items", [])
        
        # 1. Get Filters from HA States
        filter_state = hass.states.get("text.samsung_frame_slideshow_filter")
        search_query = filter_state.state.lower() if filter_state and filter_state.state not in ("unknown", "unavailable", "None") else ""
        
        fav_switch = hass.states.get("switch.samsung_frame_gallery_favorites_only")
        fav_only = fav_switch and fav_switch.state == "on"
        
        page_entity = hass.states.get("number.samsung_frame_gallery_page")
        try:
            current_page = int(float(page_entity.state)) if page_entity else 1
        except (ValueError, TypeError):
            current_page = 1

        # 2. Apply Filtering
        filtered = []
        if search_query or fav_only:
            # Parse search terms like "tree, -winter"
            terms = [t.strip() for t in search_query.split(",") if t.strip()]
            pos_terms = [t for t in terms if not t.startswith("-")]
            neg_terms = [t[1:] for t in terms if t.startswith("-") and len(t) > 1]

            for item in all_items:
                # Favorites filter
                if fav_only and not item.get("is_favorite"):
                    continue
                
                # Tag/Category filter
                tags = (item.get("tags") or "").lower()
                cat = (item.get("category") or "").lower()
                
                # Must match ANY positive term
                if pos_terms:
                    if not any(t in tags or t in cat for t in pos_terms):
                        continue
                
                # Must match NO negative terms
                if neg_terms:
                    if any(t in tags or t in cat for t in neg_terms):
                        continue
                
                filtered.append(item)
        else:
            filtered = all_items

        # 3. Paginate (25 items per page to safely fit 16KB limit)
        page_size = 25
        total_items = len(filtered)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        current_page = max(1, min(total_pages, current_page))
        
        start = (current_page - 1) * page_size
        end = start + page_size
        
        # Strip metadata further: only what the dashboard actually uses
        page_items = []
        for item in filtered[start:end]:
            page_items.append({
                "id": item.get("id"),
                "is_favorite": bool(item.get("is_favorite")),
                "category": item.get("category", "Gallery"),
                "tags": item.get("tags"),
                "source": item.get("source")
            })

        # 4. Extract Top Tags from Favorites for Quick Selection
        tag_counts = {}
        for item in all_items:
            if item.get("is_favorite"):
                tags_str = item.get("tags") or ""
                # Split by comma and clean up
                item_tags = [t.strip().lower() for t in tags_str.split(",") if t.strip()]
                for t in item_tags:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
        
        # Sort by count descending and take top 10
        # Sort by count descending and take top 10
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        top_tags = [tag for tag, count in sorted_tags[:10]]
        _LOGGER.debug(f"Deep Dive: Found {len(tag_counts)} unique tags. Top 10: {top_tags}")

        return {
            "items": page_items,
            "total_count": len(all_items),
            "filtered_count": total_items,
            "total_pages": total_pages,
            "current_page": current_page,
            "top_tags": top_tags
        }

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="samsung_frame_library",
        update_method=_fetch_library,
        update_interval=dt_util.dt.timedelta(seconds=15), # Faster update for filters
    )

    # Initial refresh
    await coordinator.async_refresh()

    async_add_entities([SamsungFrameLibrarySensor(coordinator, entry)], True)


class SamsungFrameLibrarySensor(CoordinatorEntity, SensorEntity):
    """Sensor that exposes a filtered/paged view of the Art Library."""

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Samsung Frame Art Library"
        self._attr_unique_id = f"{entry.entry_id}_art_library"
        self._attr_icon = "mdi:image-multiple-outline"

    @property
    def native_value(self) -> int:
        """Return the total count of untracked (local) items."""
        return self.coordinator.data.get("total_count", 0) if self.coordinator.data else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the paged items and metadata. Guaranteed to be under 16KB."""
        if not self.coordinator.data:
            return {}
        
        return {
            "filtered_count": self.coordinator.data.get("filtered_count", 0),
            "total_pages": self.coordinator.data.get("total_pages", 1),
            "current_page": self.coordinator.data.get("current_page", 1),
            "top_tags": self.coordinator.data.get("top_tags", []),
            "last_synced": dt_util.now().isoformat()
        }
