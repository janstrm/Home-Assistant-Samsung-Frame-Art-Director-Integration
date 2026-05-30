"""Media Source provider: browse the art library in the HA Media panel.

Each library image is exposed as a playable item; "playing" it on the
``media_player.<frame>`` entity uploads and displays it (see
``media_player.async_play_media``). Thumbnails are served by the existing
``SamsungFrameThumbnailView`` (``views.py``).
"""
from __future__ import annotations

import os
from urllib.parse import quote, unquote

from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from .const import DATA_CLIENT, DOMAIN

# MediaClass/MediaType are StrEnums; use the literal values to avoid importing
# the media_player integration just for the enum members.
_MEDIA_CLASS_DIRECTORY = "directory"
_MEDIA_CLASS_IMAGE = "image"
_MEDIA_TYPE_IMAGE = "image"
_MIME = "image/jpeg"


async def async_get_media_source(hass: HomeAssistant) -> "ArtLibraryMediaSource":
    """Set up the Samsung Frame art library media source."""
    return ArtLibraryMediaSource(hass)


def _thumbnail_url(path: str) -> str:
    # Keep slashes literal so the thumbnail HTTP view receives the absolute path.
    return f"/api/samsung_frame_art_director/thumbnail/{quote(path, safe='/')}"


class ArtLibraryMediaSource(MediaSource):
    """Expose the tagged local art library as a browsable media source."""

    name = "Samsung Frame Art"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _client(self):
        """Return the first available integration client (single device typical)."""
        for stored in self.hass.data.get(DOMAIN, {}).values():
            if isinstance(stored, dict) and (client := stored.get(DATA_CLIENT)):
                return client
        return None

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve an item to a viewable image URL (for the Media panel preview)."""
        path = unquote(item.identifier)
        return PlayMedia(_thumbnail_url(path), _MIME)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return the (single-level) list of library images."""
        children: list[BrowseMediaSource] = []
        client = self._client()
        if client is not None:
            data = await client.async_get_library_data()
            for entry in data.get("items", []):
                path = entry.get("id")
                if not path:
                    continue
                star = "★ " if entry.get("is_favorite") else ""
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        # Identifier must NOT start with "/" (HA URI rules), so
                        # URL-encode the absolute path; decoded on resolve/play.
                        identifier=quote(path, safe=""),
                        media_class=_MEDIA_CLASS_IMAGE,
                        media_content_type=_MEDIA_TYPE_IMAGE,
                        title=f"{star}{os.path.basename(path)}",
                        can_play=True,
                        can_expand=False,
                        thumbnail=_thumbnail_url(path),
                    )
                )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=_MEDIA_CLASS_DIRECTORY,
            media_content_type=_MEDIA_TYPE_IMAGE,
            title="Samsung Frame Art Library",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=_MEDIA_CLASS_IMAGE,
        )
