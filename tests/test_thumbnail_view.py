"""Behavior tests for the authenticated artwork thumbnail endpoint."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.api import SamsungFrameClient
from custom_components.samsung_frame_art_director.const import DOMAIN
from custom_components.samsung_frame_art_director.media_source import (
    ArtLibraryMediaSource,
    signed_thumbnail_url,
)
from custom_components.samsung_frame_art_director.views import (
    SamsungFrameThumbnailView,
)
from custom_components.samsung_frame_art_director.runtime import (
    SamsungFrameRuntimeData,
)


async def test_thumbnail_selects_config_entry_when_content_ids_match(
    hass,
    hass_client_no_auth,
):
    """Identical TV content IDs remain isolated between two Frames."""
    assert await async_setup_component(hass, "http", {})
    first_client = MagicMock()
    first_client.async_get_thumbnail = AsyncMock(
        return_value=(b"FIRST", "image/jpeg")
    )
    second_client = MagicMock()
    second_client.async_get_thumbnail = AsyncMock(
        return_value=(b"SECOND", "image/png")
    )
    first_entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame-a.local"})
    second_entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame-b.local"})
    first_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)
    first_entry.runtime_data = SamsungFrameRuntimeData(client=first_client)
    second_entry.runtime_data = SamsungFrameRuntimeData(client=second_client)
    hass.http.register_view(SamsungFrameThumbnailView(hass))
    client = await hass_client_no_auth()

    first = await client.get(
        signed_thumbnail_url(hass, first_entry.entry_id, "MY-SHARED")
    )
    second = await client.get(
        signed_thumbnail_url(hass, second_entry.entry_id, "MY-SHARED")
    )

    assert await first.read() == b"FIRST"
    assert first.content_type == "image/jpeg"
    assert await second.read() == b"SECOND"
    assert second.content_type == "image/png"


async def test_thumbnail_requires_home_assistant_authentication(
    hass,
    hass_client_no_auth,
):
    """Anonymous callers cannot read artwork thumbnails."""
    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(SamsungFrameThumbnailView(hass))
    client = await hass_client_no_auth()

    response = await client.get(
        "/api/samsung_frame_art_director/thumbnail/untrusted-entry/"
        "untrusted-identifier"
    )

    assert response.status == 401


@pytest.mark.parametrize(
    ("extension", "content_type"),
    [
        ("jpg", "image/jpeg"),
        ("png", "image/png"),
        ("webp", "image/webp"),
    ],
)
async def test_signed_thumbnail_uses_an_opaque_library_identifier(
    hass,
    hass_client_no_auth,
    tmp_path,
    extension,
    content_type,
):
    """Signed tracked image types work without putting their path in the URL."""
    assert await async_setup_component(hass, "http", {})
    image_bytes = b"tracked-image"
    image_path = Path(hass.config.path("www", f"tracked-art.{extension}"))
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image_bytes)

    frame_client = SamsungFrameClient(hass, "frame.local")
    frame_client.set_db_path(str(tmp_path / "frame-art.db"))
    await frame_client.async_add_local_art(
        str(image_path),
        "test",
        "Tracked test image",
        1,
        1,
        len(image_bytes),
    )

    entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame.local"})
    entry.add_to_hass(hass)
    entry.runtime_data = SamsungFrameRuntimeData(client=frame_client)

    source = ArtLibraryMediaSource(hass)
    library = await source.async_browse_media(type("Item", (), {"identifier": None})())
    item = library.children[0]

    assert str(image_path) not in item.identifier
    assert "/" not in item.identifier
    assert str(image_path).replace("\\", "/") not in item.thumbnail
    assert "authSig=" in item.thumbnail

    hass.http.register_view(SamsungFrameThumbnailView(hass))
    client = await hass_client_no_auth()
    response = await client.get(item.thumbnail)

    assert response.status == 200
    assert response.content_type == content_type
    assert await response.read() == image_bytes


@pytest.mark.parametrize(
    "identifier",
    [
        f"local-{'0' * 64}",
        "/config/secrets.yaml",
    ],
)
async def test_signed_unknown_and_path_like_identifiers_return_not_found(
    hass,
    hass_client_no_auth,
    tmp_path,
    identifier,
):
    """Authentication never turns an unknown or path-like identifier into a read."""
    assert await async_setup_component(hass, "http", {})
    frame_client = SamsungFrameClient(hass, "frame.local")
    frame_client.set_db_path(str(tmp_path / "frame-art.db"))
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame.local"})
    entry.add_to_hass(hass)
    entry.runtime_data = SamsungFrameRuntimeData(client=frame_client)
    hass.http.register_view(SamsungFrameThumbnailView(hass))
    client = await hass_client_no_auth()

    response = await client.get(
        signed_thumbnail_url(hass, entry.entry_id, identifier)
    )

    assert response.status == 404


async def test_library_hides_a_tracked_symlink_that_escapes_allowed_roots(
    hass,
    tmp_path,
):
    """Tracking a symlink does not let previews escape the library boundary."""
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    link = Path(hass.config.path("www", "escape.png"))
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError as err:
        pytest.skip(f"symlinks unavailable: {err}")

    frame_client = SamsungFrameClient(hass, "frame.local")
    frame_client.set_db_path(str(tmp_path / "frame-art.db"))
    await frame_client.async_add_local_art(
        str(link), "test", "escape", 1, 1, outside.stat().st_size
    )
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame.local"})
    entry.add_to_hass(hass)
    entry.runtime_data = SamsungFrameRuntimeData(client=frame_client)

    library = await ArtLibraryMediaSource(hass).async_browse_media(
        type("Item", (), {"identifier": None})()
    )

    assert library.children == []
