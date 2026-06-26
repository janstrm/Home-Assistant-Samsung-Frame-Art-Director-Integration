"""Tests for upload_art image sourcing: http(s) URL vs local path."""
from unittest.mock import patch

from custom_components.samsung_frame_art_director import (
    _async_read_image_bytes,
    _remote_filename,
)


class _FakeResponse:
    """Minimal aiohttp response stand-in (async context manager body)."""

    def __init__(self, data: bytes):
        self._data = data
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True

    async def read(self) -> bytes:
        return self._data


class _FakeGet:
    def __init__(self, resp: _FakeResponse):
        self._resp = resp

    async def __aenter__(self) -> _FakeResponse:
        return self._resp

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeSession:
    def __init__(self, resp: _FakeResponse):
        self._resp = resp
        self.requested_url = None
        self.timeout = None

    def get(self, url, timeout=None):
        self.requested_url = url
        self.timeout = timeout
        return _FakeGet(self._resp)


async def test_read_image_bytes_fetches_http_url(hass):
    # An http(s) URL is fetched via the shared aiohttp client (no real network).
    resp = _FakeResponse(b"JPEGDATA")
    session = _FakeSession(resp)
    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        out = await _async_read_image_bytes(hass, "https://render.local/wakeup.jpg?cache=42")

    assert out == b"JPEGDATA"
    assert session.requested_url == "https://render.local/wakeup.jpg?cache=42"
    assert resp.raised is True  # raise_for_status() is honored
    assert session.timeout is not None  # a 30s ClientTimeout is passed


def test_remote_filename_strips_url_query():
    # A ?cache-bust query must not leak into the filename tracked on the TV.
    assert _remote_filename("https://render.local/wakeup.jpg?cache=42") == "wakeup.jpg"


def test_remote_filename_plain_path_is_noop():
    # For a local path urlsplit(path).path == path, so basename is unchanged.
    assert _remote_filename("/media/frame/library/sunset.png") == "sunset.png"
    assert _remote_filename("sunset.png") == "sunset.png"
