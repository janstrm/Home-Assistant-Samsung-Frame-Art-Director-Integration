import logging
from http import HTTPStatus

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class SamsungFrameThumbnailView(HomeAssistantView):
    """View to serve artwork thumbnails."""

    url = (
        "/api/samsung_frame_art_director/thumbnail/"
        "{config_entry_id}/{content_id:.+}"
    )
    name = "api:samsung_frame_art_director:thumbnail"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize view."""
        self.hass = hass

    async def get(
        self,
        request: web.Request,
        config_entry_id: str,
        content_id: str,
    ) -> web.Response:
        """Handle GET request for thumbnail."""
        entry = self.hass.config_entries.async_get_entry(config_entry_id)
        runtime = getattr(entry, "runtime_data", None) if entry else None
        if entry is None or entry.domain != DOMAIN or runtime is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)

        thumbnail = await runtime.client.async_get_thumbnail(content_id)

        if not thumbnail:
            return web.Response(status=HTTPStatus.NOT_FOUND)

        image_data, content_type = thumbnail
        return web.Response(body=image_data, content_type=content_type)
