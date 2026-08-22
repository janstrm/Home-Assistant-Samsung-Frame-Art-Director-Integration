"""Tests for upload_art image sourcing: http(s) URL vs local path."""
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director import async_setup_entry
from custom_components.samsung_frame_art_director.const import DOMAIN


_DEFAULT_CONTENT_LENGTH = object()


class _FakeContent:
    """Minimal streamed response body."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def iter_chunked(self, _chunk_size: int):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    """Minimal aiohttp response stand-in (async context manager body)."""

    def __init__(
        self,
        data: bytes,
        content_length: int | None | object = _DEFAULT_CONTENT_LENGTH,
        chunks: list[bytes] | None = None,
    ):
        self._data = data
        self.content_length = (
            len(data) if content_length is _DEFAULT_CONTENT_LENGTH else content_length
        )
        self.content = _FakeContent(chunks if chunks is not None else [data])
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


@pytest.fixture
async def upload_service(hass):
    """Register upload_art with the TV and network boundaries replaced."""
    hass.http = MagicMock()
    client = MagicMock()
    client.host = "frame.local"
    client.token = "token"
    client.async_connect_and_pair = AsyncMock()
    client.async_upload_image = AsyncMock()
    client.async_track_art = AsyncMock()
    client.async_cleanup_storage = AsyncMock()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "token": "token"},
        options={},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            return_value=client,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.samsung_frame_art_director._reload_slideshow_timer",
            AsyncMock(),
        ),
        patch("homeassistant.components.websocket_api.async_register_command"),
    ):
        assert await async_setup_entry(hass, entry)

    yield client


async def test_upload_art_accepts_case_insensitive_https_scheme(hass, upload_service):
    """The public service accepts URL schemes regardless of letter case."""
    resp = _FakeResponse(b"JPEGDATA")
    session = _FakeSession(resp)
    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "HTTPS://render.local/wakeup.jpg?cache=42"},
            blocking=True,
        )

    assert session.requested_url == "HTTPS://render.local/wakeup.jpg?cache=42"
    assert session.timeout.total == 30
    assert resp.raised is True
    upload_service.async_upload_image.assert_awaited_once_with(
        b"JPEGDATA",
        matte="none",
    )
    upload_service.async_track_art.assert_awaited_once_with("wakeup.jpg", tags=None)


async def test_upload_art_rejects_declared_oversized_download(hass, upload_service):
    """The public service rejects a remote image larger than 20 MiB."""
    resp = _FakeResponse(b"", content_length=20 * 1024 * 1024 + 1)
    session = _FakeSession(resp)

    with (
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(ServiceValidationError, match="20 MiB"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "https://render.local/oversized.jpg"},
            blocking=True,
        )

    upload_service.async_upload_image.assert_not_awaited()


async def test_upload_art_rejects_streamed_oversized_download(hass, upload_service):
    """The size limit also applies when the server omits Content-Length."""
    one_mib = b"x" * (1024 * 1024)
    resp = _FakeResponse(b"", content_length=None, chunks=[one_mib] * 21)
    session = _FakeSession(resp)

    with (
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(ServiceValidationError, match="20 MiB"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "https://render.local/streamed.jpg"},
            blocking=True,
        )

    upload_service.async_upload_image.assert_not_awaited()


async def test_upload_art_rejects_url_without_filename(hass, upload_service):
    """A remote source needs a filename that can be tracked on the TV."""
    resp = _FakeResponse(b"JPEGDATA")
    session = _FakeSession(resp)

    with (
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(ServiceValidationError, match="filename"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "https://render.local/"},
            blocking=True,
        )

    assert session.requested_url is None
    upload_service.async_upload_image.assert_not_awaited()


async def test_upload_art_keeps_local_filename_behavior(hass, upload_service):
    """A bare filename still resolves through the existing local file path."""
    with patch("builtins.open", mock_open(read_data=b"LOCALIMAGE")):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "sunset.png"},
            blocking=True,
        )

    upload_service.async_upload_image.assert_awaited_once_with(
        b"LOCALIMAGE",
        matte="none",
    )
    upload_service.async_track_art.assert_awaited_once_with("sunset.png", tags=None)
