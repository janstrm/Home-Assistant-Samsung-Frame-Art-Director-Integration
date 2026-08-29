"""Public contract tests for library synchronization and cleanup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director import (
    _run_slideshow_job,
    async_setup,
    async_setup_entry,
)
from custom_components.samsung_frame_art_director.const import (
    CONF_SLIDESHOW_SOURCE_TYPE,
    DOMAIN,
    SLIDESHOW_SOURCE_LIBRARY,
)
from custom_components.samsung_frame_art_director.runtime import (
    SamsungFrameRuntimeData,
)


async def test_sync_library_action_reports_curator_result(hass):
    """The public action reports every completed synchronization phase."""
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    client = MagicMock()
    client.host = "frame.local"
    client.token = "token"
    client.async_connect_and_pair = AsyncMock()
    client.async_initialize_database = AsyncMock()
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


@pytest.mark.parametrize("dashboard_filter", [False, True])
async def test_slideshow_cleanup_uses_configured_options(hass, dashboard_filter):
    """The scheduled slideshow applies the same configured cleanup policy."""
    client = MagicMock()
    client.async_get_artmode_status = AsyncMock(return_value="on")
    client.async_rotate_art = AsyncMock(return_value=True)
    client.async_cleanup_storage = AsyncMock()
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_SLIDESHOW_SOURCE_TYPE: SLIDESHOW_SOURCE_LIBRARY,
            "cleanup_max_items": 7,
            "cleanup_max_age_days": 14,
            "cleanup_preserve_current": False,
            "cleanup_dry_run": True,
        },
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SamsungFrameRuntimeData(client=client)
    if dashboard_filter:
        hass.states.async_set("switch.samsung_frame_gallery_favorites_only", "on")

    await _run_slideshow_job(hass, entry)

    client.async_cleanup_storage.assert_awaited_once_with(
        max_items=7,
        max_age_days=14,
        preserve_current=False,
        only_integration_managed=True,
        dry_run=True,
    )


async def test_manual_cleanup_action_uses_complete_default_policy(hass):
    """The public cleanup action and automatic paths share safe defaults."""
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    client = MagicMock()
    client.host = "frame.local"
    client.token = "token"
    client.async_connect_and_pair = AsyncMock()
    client.async_initialize_database = AsyncMock()
    client.async_cleanup_storage = AsyncMock()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "token": "token"},
    )
    entry.add_to_hass(hass)

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
    ):
        assert await async_setup_entry(hass, entry)
        await hass.services.async_call(DOMAIN, "cleanup_storage", blocking=True)

    client.async_cleanup_storage.assert_awaited_once_with(
        max_items=50,
        max_age_days=None,
        preserve_current=True,
        only_integration_managed=True,
        dry_run=False,
    )
