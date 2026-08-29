"""Recorder contract tests for the art-library sensor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import MATCH_ALL
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.const import DOMAIN
from custom_components.samsung_frame_art_director.runtime import (
    SamsungFrameRuntimeData,
)
from custom_components.samsung_frame_art_director.sensor import (
    SamsungFrameLibrarySensor,
    async_setup_entry,
)


async def test_library_sensor_uses_renamed_filter_owned_by_its_entry(hass):
    """Each Frame library follows its own stable filter entity identity."""
    client = MagicMock()
    client.async_get_library_data = AsyncMock(
        return_value={
            "items": [
                {
                    "id": "local-nature",
                    "is_favorite": False,
                    "category": "Landscape",
                    "tags": "nature, green",
                    "name": "nature.png",
                },
                {
                    "id": "local-city",
                    "is_favorite": False,
                    "category": "Urban",
                    "tags": "city, night",
                    "name": "city.png",
                },
            ]
        }
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local"},
        unique_id="frame-filter",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SamsungFrameRuntimeData(client=client)
    filter_entity = er.async_get(hass).async_get_or_create(
        "text",
        DOMAIN,
        f"{entry.entry_id}_slideshow_filter",
        config_entry=entry,
        suggested_object_id="renamed_frame_filter",
    )
    hass.states.async_set(filter_entity.entity_id, "nature")
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    sensor = add_entities.call_args.args[0][0]
    assert sensor.coordinator.data["filtered_count"] == 1
    assert [item["id"] for item in sensor.coordinator.data["items"]] == [
        "local-nature"
    ]


def test_large_gallery_stays_live_without_entering_recorder_history():
    """Oversized dashboard data remains live but is excluded from Recorder."""
    items = [
        {
            "id": f"local-{index:08d}",
            "is_favorite": index % 2 == 0,
            "category": "Gallery",
            "tags": ", ".join([f"descriptive-tag-{index}-{tag}" for tag in range(30)]),
            "name": f"descriptive-artwork-name-{index}.png",
            "thumbnail": f"/api/samsung_frame_art_director/thumbnail/local-{index:08d}?authSig={'x' * 180}",
        }
        for index in range(25)
    ]
    coordinator = MagicMock()
    coordinator.data = {
        "items": items,
        "total_count": 125,
        "filtered_count": 125,
        "total_pages": 5,
        "current_page": 1,
        "top_tags": [f"tag-{index}" for index in range(10)],
    }
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame.local"})

    entity = SamsungFrameLibrarySensor(coordinator, entry)
    live_attributes = entity.extra_state_attributes

    assert entity.native_value == 125
    assert live_attributes["items"] == items
    assert len(json.dumps(live_attributes)) > 16_384
    assert MATCH_ALL in entity._unrecorded_attributes
