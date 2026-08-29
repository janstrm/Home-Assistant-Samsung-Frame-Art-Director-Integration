"""Regression tests for independent Samsung Frame config entries."""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.samsung_frame_art_director.api import DeviceUnavailableError
from custom_components.samsung_frame_art_director.const import DB_DIR, DB_FILE, DOMAIN
from custom_components.samsung_frame_art_director.media_player import (
    async_setup_entry as async_setup_media_player,
)
from custom_components.samsung_frame_art_director.runtime import (
    SamsungFrameRuntimeData,
)


def _client(host: str) -> MagicMock:
    client = MagicMock()
    client.host = host
    client.token = "SAVED"
    client.async_initialize_database = AsyncMock()
    client.async_connect_and_pair = AsyncMock()
    client.async_read_local_art = AsyncMock(
        return_value={
            "data": b"TRACKEDIMAGE",
            "path": "/media/frame/library/tracked.png",
            "content_type": "image/png",
        }
    )
    client.async_upload_image = AsyncMock(return_value=f"MY-{host}")
    client.async_cleanup_storage = AsyncMock()
    client.async_purge_database = AsyncMock()
    client.async_toggle_favorite = AsyncMock(return_value=True)
    client.async_disconnect = AsyncMock()
    return client


async def test_media_player_coordinator_is_owned_by_its_config_entry(hass):
    """The media-player platform needs no global hass.data client mirror."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "token": "SAVED"},
        unique_id="frame-runtime",
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = _client("frame.local")
    client.async_get_state = AsyncMock(
        side_effect=[
            {"status": "on", "content_id": "MY-FRAME"},
            DeviceUnavailableError("offline"),
            {"status": "on", "content_id": "MY-RECOVERED"},
        ]
    )
    entry.runtime_data = SamsungFrameRuntimeData(client=client)
    add_entities = MagicMock()

    await async_setup_media_player(hass, entry, add_entities)

    assert entry.runtime_data.coordinator is not None
    assert entry.runtime_data.coordinator.data == {
        "status": "on",
        "content_id": "MY-FRAME",
    }
    add_entities.assert_called_once()
    entity = add_entities.call_args.args[0][0]
    assert entity.available is True

    await entry.runtime_data.coordinator.async_refresh()
    assert entity.available is False

    await entry.runtime_data.coordinator.async_refresh()
    assert entity.available is True
    assert entry.runtime_data.coordinator.data == {
        "status": "on",
        "content_id": "MY-RECOVERED",
    }


async def test_setup_migrates_legacy_database_into_entry_owned_database(
    hass, tmp_path
):
    """Per-Frame isolation preserves data from the previous shared database."""
    legacy_db_path = tmp_path / DB_DIR / DB_FILE
    legacy_db_path.parent.mkdir(parents=True)
    with sqlite3.connect(legacy_db_path) as connection:
        connection.execute("CREATE TABLE migration_marker (value TEXT)")
        connection.execute(
            "INSERT INTO migration_marker (value) VALUES (?)", ("preserved",)
        )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "token": "SAVED"},
        unique_id="frame-migration",
    )
    entry.add_to_hass(hass)
    client = _client("frame.local")

    with (
        patch.object(
            hass.config,
            "path",
            side_effect=lambda relative_path: str(tmp_path / relative_path),
        ),
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            return_value=client,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.samsung_frame_art_director._reload_slideshow_timer",
            AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)

    entry_db_path = Path(client.set_db_path.call_args.args[0])
    assert entry_db_path.exists()
    with sqlite3.connect(entry_db_path) as connection:
        marker = connection.execute(
            "SELECT value FROM migration_marker"
        ).fetchone()
    assert marker == ("preserved",)


async def test_unloading_one_frame_keeps_actions_for_the_remaining_runtime(hass):
    """An unloaded runtime cannot make the remaining Frame target ambiguous."""
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    first_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame-a.local", "token": "SAVED"},
        unique_id="frame-unload-a",
    )
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame-b.local", "token": "SAVED"},
        unique_id="frame-unload-b",
    )
    first_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)
    first_client = _client("frame-a.local")
    second_client = _client("frame-b.local")

    with (
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            side_effect=[first_client, second_client],
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.samsung_frame_art_director._reload_slideshow_timer",
            AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, first_entry)
        assert await async_setup_entry(hass, second_entry)
        assert await async_unload_entry(hass, first_entry)

    assert hass.services.has_service(DOMAIN, "purge_database")
    await hass.services.async_call(DOMAIN, "purge_database", blocking=True)

    first_client.async_disconnect.assert_awaited_once_with()
    first_client.async_purge_database.assert_not_awaited()
    second_client.async_purge_database.assert_awaited_once_with()


async def test_change_gallery_page_uses_renamed_controls_of_targeted_frame(hass):
    """Gallery paging follows stable entry ownership after entity renames."""
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "token": "SAVED"},
        unique_id="frame-gallery",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SamsungFrameRuntimeData(client=_client("frame.local"))

    registry = er.async_get(hass)
    target = registry.async_get_or_create(
        "media_player",
        DOMAIN,
        "frame-gallery",
        config_entry=entry,
        suggested_object_id="renamed_frame",
    )
    page = registry.async_get_or_create(
        "number",
        DOMAIN,
        f"{entry.entry_id}_gallery_page",
        config_entry=entry,
        suggested_object_id="renamed_gallery_page",
    )
    library = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_art_library",
        config_entry=entry,
        suggested_object_id="renamed_art_library",
    )
    hass.states.async_set(page.entity_id, "2")
    hass.states.async_set(library.entity_id, "60")

    set_value = AsyncMock()
    hass.services.async_register("number", "set_value", set_value)
    await hass.services.async_call(
        DOMAIN,
        "change_gallery_page",
        {"entity_id": target.entity_id, "step": 1},
        blocking=True,
    )
    await hass.async_block_till_done()

    set_value.assert_awaited_once()
    set_value_call = set_value.await_args.args[0]
    assert set_value_call.data == {"entity_id": page.entity_id, "value": 3}


async def test_targeted_actions_use_only_the_selected_frames_runtime_and_options(
    hass,
):
    """Targeted actions cannot leak calls or options between two Frames."""
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    first_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame-a.local", "token": "SAVED"},
        options={
            "matte_style": "shadowbox",
            "matte_color": "polar",
            "cleanup_max_items": 11,
        },
        unique_id="frame-a",
    )
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame-b.local", "token": "SAVED"},
        options={
            "matte_style": "none",
            "cleanup_max_items": 22,
        },
        unique_id="frame-b",
    )
    first_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)
    first_client = _client("frame-a.local")
    second_client = _client("frame-b.local")

    with (
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            side_effect=[first_client, second_client],
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.samsung_frame_art_director._reload_slideshow_timer",
            AsyncMock(),
        ),
        patch("homeassistant.components.websocket_api.async_register_command"),
    ):
        assert await async_setup_entry(hass, first_entry)
        assert await async_setup_entry(hass, second_entry)

    first_db_path = first_client.set_db_path.call_args.args[0]
    second_db_path = second_client.set_db_path.call_args.args[0]
    assert first_db_path != second_db_path
    assert first_entry.entry_id in first_db_path
    assert second_entry.entry_id in second_db_path

    entity = er.async_get(hass).async_get_or_create(
        "media_player",
        DOMAIN,
        "frame-a",
        config_entry=first_entry,
        suggested_object_id="frame_a",
    )
    media_id = f"local-{'a' * 64}"

    await hass.services.async_call(
        DOMAIN,
        "upload_art",
        {"entity_id": entity.entity_id, "path": media_id},
        blocking=True,
    )

    first_client.async_upload_image.assert_awaited_once_with(
        b"TRACKEDIMAGE",
        matte="shadowbox_polar",
        source_file="/media/frame/library/tracked.png",
        tags=None,
    )
    first_client.async_cleanup_storage.assert_awaited_once_with(
        max_items=11,
        max_age_days=None,
        preserve_current=True,
        only_integration_managed=True,
        dry_run=False,
    )
    second_client.async_read_local_art.assert_not_awaited()
    second_client.async_upload_image.assert_not_awaited()
    second_client.async_cleanup_storage.assert_not_awaited()

    first_client.async_cleanup_storage.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "cleanup_storage",
        {"entity_id": entity.entity_id},
        blocking=True,
    )
    first_client.async_cleanup_storage.assert_awaited_once_with(
        max_items=11,
        max_age_days=None,
        preserve_current=True,
        only_integration_managed=True,
        dry_run=False,
    )
    second_client.async_cleanup_storage.assert_not_awaited()

    await hass.services.async_call(
        DOMAIN,
        "purge_database",
        {"entity_id": entity.entity_id},
        blocking=True,
    )

    first_client.async_purge_database.assert_awaited_once_with()
    second_client.async_purge_database.assert_not_awaited()

    await hass.services.async_call(
        DOMAIN,
        "toggle_favorite",
        {"entity_id": entity.entity_id, "content_id": "MY-SHARED-ID"},
        blocking=True,
    )

    first_client.async_toggle_favorite.assert_awaited_once_with("MY-SHARED-ID")
    second_client.async_toggle_favorite.assert_not_awaited()
