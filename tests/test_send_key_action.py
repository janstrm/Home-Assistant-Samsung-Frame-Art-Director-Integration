"""Tests for the public send_key Home Assistant action."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director import async_setup, async_setup_entry
from custom_components.samsung_frame_art_director.const import DOMAIN


@pytest.fixture
async def send_key_action(hass):
    """Register the action with one loaded Frame and return its boundary."""
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    client = MagicMock()
    client.host = "frame.local"
    client.token = "token"
    client.async_connect_and_pair = AsyncMock()
    client.async_initialize_database = AsyncMock()
    client.async_send_key = AsyncMock()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "token": "token"},
        unique_id="send-key-frame",
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
    ):
        assert await async_setup_entry(hass, entry)

    entity = er.async_get(hass).async_get_or_create(
        "media_player",
        DOMAIN,
        "send-key-frame",
        config_entry=entry,
        suggested_object_id="send_key_frame",
    )
    return client, entity.entity_id


async def test_send_key_action_taps_key_on_selected_frame(hass, send_key_action):
    """A target and key are forwarded as a regular tap."""
    client, entity_id = send_key_action

    await hass.services.async_call(
        DOMAIN,
        "send_key",
        {"entity_id": entity_id, "key": "KEY_HDMI"},
        blocking=True,
    )

    client.async_send_key.assert_awaited_once_with("KEY_HDMI", hold_seconds=None)


async def test_send_key_action_forwards_hold_duration(hass, send_key_action):
    """A hold duration reaches the TV boundary without being converted to taps."""
    client, entity_id = send_key_action

    await hass.services.async_call(
        DOMAIN,
        "send_key",
        {"entity_id": entity_id, "key": "KEY_VOLUP", "hold_seconds": 0.8},
        blocking=True,
    )

    client.async_send_key.assert_awaited_once_with("KEY_VOLUP", hold_seconds=0.8)


@pytest.mark.parametrize(
    "data",
    [
        {"key": "HDMI"},
        {"key": "KEY_HDMI", "hold_seconds": 0},
        {"key": "KEY_HDMI", "hold_seconds": 30.1},
    ],
)
async def test_send_key_action_rejects_invalid_input(hass, send_key_action, data):
    """Malformed keys and unsafe hold durations fail at the action schema."""
    _, entity_id = send_key_action

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "send_key",
            {"entity_id": entity_id, **data},
            blocking=True,
        )
