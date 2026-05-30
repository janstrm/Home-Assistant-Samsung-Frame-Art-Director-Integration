"""Tests for the Media Source provider (browse + resolve)."""
import types
from urllib.parse import quote, unquote

from custom_components.samsung_frame_art_director.media_source import (
    ArtLibraryMediaSource,
    async_get_media_source,
)


class _FakeClient:
    async def async_get_library_data(self):
        return {
            "items": [
                {
                    "id": "/media/frame/library/a.jpg",
                    "tags": "nature",
                    "is_favorite": True,
                    "source": "/media/frame/library/a.jpg",
                },
                {
                    "id": "/media/frame/library/b.png",
                    "tags": "city",
                    "is_favorite": False,
                    "source": "/media/frame/library/b.png",
                },
            ]
        }


async def test_factory_returns_source(hass):
    source = await async_get_media_source(hass)
    assert isinstance(source, ArtLibraryMediaSource)
    assert source.domain == "samsung_frame_art_director"


async def test_browse_lists_library_items(hass):
    source = ArtLibraryMediaSource(hass)
    source._client = lambda: _FakeClient()

    result = await source.async_browse_media(types.SimpleNamespace(identifier=None))

    assert result.can_expand is True
    assert len(result.children) == 2
    first = result.children[0]
    assert first.can_play is True
    # Identifier must be URL-encoded (HA rejects identifiers starting with "/").
    assert not first.identifier.startswith("/")
    assert unquote(first.identifier) == "/media/frame/library/a.jpg"
    assert first.title.startswith("★")  # favorite marker
    assert first.thumbnail.startswith("/api/samsung_frame_art_director/thumbnail/")


async def test_browse_without_client_is_empty(hass):
    source = ArtLibraryMediaSource(hass)
    source._client = lambda: None
    result = await source.async_browse_media(types.SimpleNamespace(identifier=None))
    assert result.children == []


async def test_resolve_returns_image_url(hass):
    source = ArtLibraryMediaSource(hass)
    media = await source.async_resolve_media(
        types.SimpleNamespace(identifier=quote("/media/frame/library/a.jpg", safe=""))
    )
    assert media.mime_type == "image/jpeg"
    assert media.url.startswith("/api/samsung_frame_art_director/thumbnail/")
