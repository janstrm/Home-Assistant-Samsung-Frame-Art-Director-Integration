"""Media Source provider: browse the art library in the HA Media panel.

Each library image is exposed as a playable item; "playing" it on the
``media_player.<frame>`` entity uploads and displays it (see
``media_player.async_play_media``). Thumbnails are served by the existing
``SamsungFrameThumbnailView`` (``views.py``).
"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .targets import (
    FrameActionTarget,
    loaded_frame_target,
    loaded_frame_targets,
)

# MediaClass/MediaType are StrEnums; use the literal values to avoid importing
# the media_player integration just for the enum members.
_MEDIA_CLASS_DIRECTORY = "directory"
_MEDIA_CLASS_IMAGE = "image"
_MEDIA_TYPE_IMAGE = "image"
_FALLBACK_MIME = "application/octet-stream"


async def async_get_media_source(hass: HomeAssistant) -> "ArtLibraryMediaSource":
    """Set up the Samsung Frame art library media source."""
    return ArtLibraryMediaSource(hass)


def signed_thumbnail_url(
    hass: HomeAssistant,
    config_entry_id: str,
    media_id: str,
) -> str:
    """Build a thumbnail URL from an opaque, database-backed media ID."""
    path = (
        "/api/samsung_frame_art_director/thumbnail/"
        f"{quote(config_entry_id, safe='')}/{quote(media_id, safe='')}"
    )
    return async_sign_path(
        hass,
        path,
        timedelta(minutes=5),
        use_content_user=True,
    )


def media_identifier(config_entry_id: str, media_id: str) -> str:
    """Namespace an opaque local-art ID by the Frame that listed it."""
    return f"{config_entry_id}:{media_id}"


def split_media_identifier(identifier: str) -> tuple[str | None, str]:
    """Split a namespaced identifier while accepting legacy local-art IDs."""
    config_entry_id, separator, media_id = identifier.partition(":")
    if separator:
        return config_entry_id, media_id
    return None, identifier


class ArtLibraryMediaSource(MediaSource):
    """Expose the tagged local art library as a browsable media source."""

    name = "Samsung Frame Art"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _target(
        self, identifier: str
    ) -> tuple[FrameActionTarget | None, str]:
        """Resolve a namespaced item to its loaded Frame and opaque media ID."""
        config_entry_id, media_id = split_media_identifier(identifier)
        targets = loaded_frame_targets(self.hass)
        if config_entry_id:
            return loaded_frame_target(self.hass, config_entry_id), media_id
        return (targets[0] if len(targets) == 1 else None, media_id)

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve an item to a viewable image URL (for the Media panel preview)."""
        content_type = _FALLBACK_MIME
        target, media_id = self._target(item.identifier)
        if target:
            data = await target.runtime.client.async_get_library_data()
            content_type = next(
                (
                    entry.get("content_type", _FALLBACK_MIME)
                    for entry in data.get("items", [])
                    if entry.get("id") == media_id
                ),
                _FALLBACK_MIME,
            )
            return PlayMedia(
                signed_thumbnail_url(
                    self.hass,
                    target.entry.entry_id,
                    media_id,
                ),
                content_type,
            )
        return PlayMedia("", content_type)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return the (single-level) list of library images."""
        children: list[BrowseMediaSource] = []
        targets = loaded_frame_targets(self.hass)
        for target in targets:
            data = await target.runtime.client.async_get_library_data()
            for entry in data.get("items", []):
                media_id = entry.get("id")
                if not media_id:
                    continue
                star = "★ " if entry.get("is_favorite") else ""
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=media_identifier(
                            target.entry.entry_id, media_id
                        ),
                        media_class=_MEDIA_CLASS_IMAGE,
                        media_content_type=entry.get("content_type", _MEDIA_TYPE_IMAGE),
                        title=(
                            f"{target.entry.title}: " if len(targets) > 1 else ""
                        )
                        + f"{star}{entry.get('name') or media_id}",
                        can_play=True,
                        can_expand=False,
                        thumbnail=signed_thumbnail_url(
                            self.hass,
                            target.entry.entry_id,
                            media_id,
                        ),
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
