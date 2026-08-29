"""Behavior tests for the authenticated artwork thumbnail endpoint."""

from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.api import SamsungFrameClient
from custom_components.samsung_frame_art_director.const import DATA_CLIENT, DOMAIN
from custom_components.samsung_frame_art_director.media_source import (
    ArtLibraryMediaSource,
)
from custom_components.samsung_frame_art_director.views import (
    SamsungFrameThumbnailView,
)


async def test_thumbnail_requires_home_assistant_authentication(
    hass,
    hass_client_no_auth,
):
    """Anonymous callers cannot read artwork thumbnails."""
    hass.http.register_view(SamsungFrameThumbnailView(hass))
    client = await hass_client_no_auth()

    response = await client.get(
        "/api/samsung_frame_art_director/thumbnail/untrusted-identifier"
    )

    assert response.status == 401


async def test_signed_thumbnail_uses_an_opaque_library_identifier(
    hass,
    hass_client_no_auth,
):
    """A signed tracked PNG works without putting its filesystem path in the URL."""
    image_bytes = b"\x89PNG\r\n\x1a\ntracked-image"
    image_path = Path(hass.config.path("www", "tracked-art.png"))
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image_bytes)

    frame_client = SamsungFrameClient(hass, "frame.local")
    frame_client.set_db_path(hass.config.path(".storage", "frame-art.db"))
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
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_CLIENT: frame_client,
    }

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
    assert response.content_type == "image/png"
    assert await response.read() == image_bytes


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
    frame_client.set_db_path(hass.config.path(".storage", "frame-art.db"))
    await frame_client.async_add_local_art(
        str(link), "test", "escape", 1, 1, outside.stat().st_size
    )
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame.local"})
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_CLIENT: frame_client,
    }

    library = await ArtLibraryMediaSource(hass).async_browse_media(
        type("Item", (), {"identifier": None})()
    )

    assert library.children == []
