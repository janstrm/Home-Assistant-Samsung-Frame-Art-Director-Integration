"""Public contract tests for library synchronization and cleanup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director import async_setup_entry
from custom_components.samsung_frame_art_director.const import DOMAIN


async def test_sync_library_action_reports_curator_result(hass):
    """The public action reports every completed synchronization phase."""
    hass.http = MagicMock()
    client = MagicMock()
    client.host = "frame.local"
    client.token = "token"
    client.async_connect_and_pair = AsyncMock()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "token": "token"},
    )
    entry.add_to_hass(hass)

    curator = MagicMock()
    curator.async_sync_library = AsyncMock(
        return_value={
            "added": 2,
            "stale_removed": 3,
            "duplicates_removed": 1,
        }
    )

    with (
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            return_value=client,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.samsung_frame_art_director._reload_slideshow_timer",
            AsyncMock(),
        ),
        patch("homeassistant.components.websocket_api.async_register_command"),
        patch(
            "custom_components.samsung_frame_art_director.curator.ContentCurator",
            return_value=curator,
        ),
        patch(
            "custom_components.samsung_frame_art_director.persistent_notification.async_create"
        ) as create_notification,
    ):
        assert await async_setup_entry(hass, entry)
        await hass.services.async_call(DOMAIN, "sync_library", blocking=True)

    create_notification.assert_called_once_with(
        hass,
        "Library sync complete: 2 added, 3 stale removed, 1 duplicate removed.",
        title="Art Director",
    )
