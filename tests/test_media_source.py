"""Tests for the Media Source provider (browse + resolve)."""
import types

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.const import DOMAIN
from custom_components.samsung_frame_art_director.media_source import (
    ArtLibraryMediaSource,
    async_get_media_source,
)
from custom_components.samsung_frame_art_director.runtime import (
    SamsungFrameRuntimeData,
)


class _FakeClient:
    async def async_get_library_data(self):
        return {
            "items": [
                {
                    "id": "local-aaaaaaaa",
                    "tags": "nature",
                    "is_favorite": True,
                    "name": "a.jpg",
                    "content_type": "image/jpeg",
                },
                {
                    "id": "local-bbbbbbbb",
                    "tags": "city",
                    "is_favorite": False,
                    "name": "b.png",
                    "content_type": "image/png",
                },
                {
                    "id": "local-cccccccc",
                    "tags": "abstract",
                    "is_favorite": False,
                    "name": "c.webp",
                    "content_type": "image/webp",
                },
            ]
        }


def _source_with_client(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "frame.local"})
    entry.add_to_hass(hass)
    entry.runtime_data = SamsungFrameRuntimeData(client=_FakeClient())
    return ArtLibraryMediaSource(hass), entry


async def test_factory_returns_source(hass):
    source = await async_get_media_source(hass)
    assert isinstance(source, ArtLibraryMediaSource)
    assert source.domain == "samsung_frame_art_director"


async def test_browse_lists_library_items(hass):
    assert await async_setup_component(hass, "http", {})
    source, entry = _source_with_client(hass)

    result = await source.async_browse_media(types.SimpleNamespace(identifier=None))

    assert result.can_expand is True
    assert len(result.children) == 3
    first = result.children[0]
    assert first.can_play is True
    assert first.identifier == f"{entry.entry_id}:local-aaaaaaaa"
    assert first.title.startswith("★")  # favorite marker
    assert first.thumbnail.startswith("/api/samsung_frame_art_director/thumbnail/")
    assert [child.media_content_type for child in result.children] == [
        "image/jpeg",
        "image/png",
        "image/webp",
    ]


async def test_browse_without_client_is_empty(hass):
    source = ArtLibraryMediaSource(hass)
    result = await source.async_browse_media(types.SimpleNamespace(identifier=None))
    assert result.children == []


@pytest.mark.parametrize(
    ("media_id", "content_type"),
    [
        ("local-aaaaaaaa", "image/jpeg"),
        ("local-bbbbbbbb", "image/png"),
        ("local-cccccccc", "image/webp"),
    ],
)
async def test_resolve_returns_image_url(hass, media_id, content_type):
    assert await async_setup_component(hass, "http", {})
    source, entry = _source_with_client(hass)
    media = await source.async_resolve_media(
        types.SimpleNamespace(identifier=f"{entry.entry_id}:{media_id}")
    )
    assert media.mime_type == content_type
    assert f"/{media_id}?" in media.url
    assert "authSig=" in media.url
