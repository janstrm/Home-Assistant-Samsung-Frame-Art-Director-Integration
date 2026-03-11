import logging
import os
from http import HTTPStatus
from typing import Optional

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, DATA_CLIENT

_LOGGER = logging.getLogger(__name__)

class SamsungFrameThumbnailView(HomeAssistantView):
    """View to serve artwork thumbnails."""

    url = "/api/samsung_frame_art_director/thumbnail/{content_id:.+}"
    name = "api:samsung_frame_art_director:thumbnail"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize view."""
        self.hass = hass

    async def get(self, request: web.Request, content_id: str) -> web.Response:
        """Handle GET request for thumbnail."""
        # Find the client (we need ANY client, or better, the DB path)
        # Since this is a global view, we need to locate the integration instance.
        # We can iterate loaded entries.
        
        # 1. Use content_id as-is (it might be a file path)
        clean_id = content_id
        
        # 2. Locate DB
        db_path = None
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

        # 3. Ask Client for Image Path
        # We need a synchronous method to query DB for path? 
        # Or just use the client to get the bytes.
        
        image_data = await client.async_get_thumbnail(clean_id)
        
        if not image_data:
            return web.Response(status=HTTPStatus.NOT_FOUND)

        return web.Response(body=image_data, content_type="image/jpeg")
