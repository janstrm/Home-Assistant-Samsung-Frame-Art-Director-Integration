"""Media Source provider: browse the art library in the HA Media panel.

Each library image is exposed as a playable item; "playing" it on the
``media_player.<frame>`` entity uploads and displays it (see
``media_player.async_play_media``). Thumbnails are served by the existing
``SamsungFrameThumbnailView`` (``views.py``).
"""
from __future__ import annotations

from datetime import timedelta
import os
from urllib.parse import quote

from homeassistant.components.http.auth import async_sign_path
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


def signed_thumbnail_url(hass: HomeAssistant, media_id: str) -> str:
    """Build a thumbnail URL from an opaque, database-backed media ID."""
    path = f"/api/samsung_frame_art_director/thumbnail/{quote(media_id, safe='')}"
    return async_sign_path(
        hass,
        path,
        timedelta(minutes=5),
        use_content_user=True,
    )


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
        return PlayMedia(signed_thumbnail_url(self.hass, item.identifier), _MIME)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return the (single-level) list of library images."""
        children: list[BrowseMediaSource] = []
        client = self._client()
        if client is not None:
            data = await client.async_get_library_data()
            for entry in data.get("items", []):
                media_id = entry.get("id")
                if not media_id:
                    continue
                star = "★ " if entry.get("is_favorite") else ""
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=media_id,
                        media_class=_MEDIA_CLASS_IMAGE,
                        media_content_type=_MEDIA_TYPE_IMAGE,
                        title=f"{star}{os.path.basename(entry.get('source') or media_id)}",
                        can_play=True,
                        can_expand=False,
                        thumbnail=signed_thumbnail_url(self.hass, media_id),
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
