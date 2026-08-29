"""Recorder contract tests for the art-library sensor."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from homeassistant.const import MATCH_ALL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.const import DOMAIN
from custom_components.samsung_frame_art_director.sensor import (
    SamsungFrameLibrarySensor,
)


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
