import logging
from http import HTTPStatus

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, DATA_CLIENT

_LOGGER = logging.getLogger(__name__)

class SamsungFrameThumbnailView(HomeAssistantView):
    """View to serve artwork thumbnails."""

    url = "/api/samsung_frame_art_director/thumbnail/{content_id:.+}"
    name = "api:samsung_frame_art_director:thumbnail"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize view."""
        self.hass = hass

    async def get(self, request: web.Request, content_id: str) -> web.Response:
        """Handle GET request for thumbnail."""
        client = None

        # Find loaded config entry
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        
        # Use the first loaded one
        entry = entries[0]
        data = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if data:
            client = data.get(DATA_CLIENT)
            
        if not client:
             return web.Response(status=HTTPStatus.NOT_FOUND)

        thumbnail = await client.async_get_thumbnail(content_id)

        if not thumbnail:
            return web.Response(status=HTTPStatus.NOT_FOUND)

        image_data, content_type = thumbnail
        return web.Response(body=image_data, content_type=content_type)
