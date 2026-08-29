"""Security behavior for artwork selected through the Media panel."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.api import SamsungFrameClient
from custom_components.samsung_frame_art_director.const import DATA_CLIENT, DOMAIN
from custom_components.samsung_frame_art_director.media_player import (
    SamsungFrameMediaPlayer,
)


def _media_player(hass, entry, client):
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_CLIENT: client}
    coordinator = DataUpdateCoordinator(
        hass,
        logging.getLogger(__name__),
        name="test-frame",
    )
    return SamsungFrameMediaPlayer(hass, entry, coordinator)


async def test_media_player_rejects_a_path_bearing_media_identifier(hass, tmp_path):
    """A crafted Media Source URI cannot make the entity read an arbitrary path."""
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")
    client = SamsungFrameClient(hass, "frame.local")
    client.set_db_path(str(tmp_path / "art.db"))
    client.async_upload_image = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame.local"})
    entry.add_to_hass(hass)
    entity = _media_player(hass, entry, client)
    media_id = f"media-source://{DOMAIN}/{quote(str(outside), safe='')}"

    with pytest.raises(HomeAssistantError, match="tracked local library"):
        await entity.async_play_media("image", media_id)

    client.async_upload_image.assert_not_awaited()


async def test_media_player_uploads_a_tracked_opaque_library_item(hass, tmp_path):
    """The Media panel still uploads a database-backed local artwork."""
    image_bytes = b"tracked-image"
    image_path = Path(hass.config.path("www", "play.png"))
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image_bytes)
    client = SamsungFrameClient(hass, "frame.local")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_add_local_art(
        str(image_path), "test", "play", 1, 1, len(image_bytes)
    )
    media_id = (await client.async_get_library_data())["items"][0]["id"]
    client.async_upload_image = AsyncMock(return_value="MY-TRACKED")
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame.local"})
    entry.add_to_hass(hass)
    entity = _media_player(hass, entry, client)

    await entity.async_play_media(
        "image",
        f"media-source://{DOMAIN}/{media_id}",
    )

    client.async_upload_image.assert_awaited_once_with(
        image_bytes,
        matte="none",
        source_file=str(image_path.resolve()),
    )
