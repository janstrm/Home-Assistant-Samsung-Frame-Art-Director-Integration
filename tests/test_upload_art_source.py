"""Tests for upload_art image sourcing: http(s) URL vs local path."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, mock_open, patch

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
    client.async_get_artmode_status = AsyncMock()
    client.async_send_key = AsyncMock()
    client.async_set_artmode = AsyncMock()
    client.async_upload_image = AsyncMock()
    client.async_track_art = AsyncMock()
    client.async_cleanup_storage = AsyncMock()
    client.async_delete_art = AsyncMock()
    client.async_read_local_art = AsyncMock()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "token": "token"},
        options={"use_power_key_on_off": True},
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
        source_file="HTTPS://render.local/wakeup.jpg?cache=42",
        tags=None,
    )
    upload_service.async_track_art.assert_not_awaited()


async def test_upload_art_rejects_an_untrusted_remote_host(hass, upload_service):
    """The public service must not contact a URL outside HA's allowlist."""
    resp = _FakeResponse(b"JPEGDATA")
    session = _FakeSession(resp)

    with (
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(ServiceValidationError, match="allowlist_external_urls"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "https://untrusted.example/image.jpg"},
            blocking=True,
        )

    assert session.requested_url is None
    upload_service.async_upload_image.assert_not_awaited()


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
    with (
        patch("pathlib.Path.is_file", return_value=True),
        patch("pathlib.Path.open", mock_open(read_data=b"LOCALIMAGE")),
    ):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "sunset.png"},
            blocking=True,
        )

    upload_service.async_upload_image.assert_awaited_once_with(
        b"LOCALIMAGE",
        matte="none",
        source_file="sunset.png",
        tags=None,
    )
    upload_service.async_track_art.assert_not_awaited()


async def test_upload_art_accepts_a_tracked_opaque_library_id(hass, upload_service):
    """The gallery can upload a tracked item without exposing its filesystem path."""
    media_id = f"local-{'a' * 64}"
    upload_service.async_read_local_art.return_value = {
        "data": b"TRACKEDIMAGE",
        "path": "/media/frame/library/tracked.png",
        "content_type": "image/png",
    }

    await hass.services.async_call(
        DOMAIN,
        "upload_art",
        {"path": media_id},
        blocking=True,
    )

    upload_service.async_read_local_art.assert_awaited_once_with(media_id)
    upload_service.async_upload_image.assert_awaited_once_with(
        b"TRACKEDIMAGE",
        matte="none",
        source_file="/media/frame/library/tracked.png",
        tags=None,
    )


async def test_upload_art_reads_a_file_through_the_config_alias(
    hass,
    upload_service,
):
    """The documented /config path remains compatible after canonicalization."""
    image_path = Path(hass.config.path("www", "frame-test.png"))
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"CONFIGIMAGE")

    await hass.services.async_call(
        DOMAIN,
        "upload_art",
        {"path": "/config/www/frame-test.png"},
        blocking=True,
    )

    upload_service.async_upload_image.assert_awaited_once_with(
        b"CONFIGIMAGE",
        matte="none",
        source_file="/config/www/frame-test.png",
        tags=None,
    )


async def test_upload_art_rejects_config_traversal(hass, upload_service):
    """A /config alias cannot escape into a similarly named sibling directory."""
    with pytest.raises(ServiceValidationError, match="outside the allowed"):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "/config/../config-secret/credentials.png"},
            blocking=True,
        )

    upload_service.async_upload_image.assert_not_awaited()


async def test_upload_art_rejects_absolute_prefix_collision(hass, upload_service):
    """A directory whose name merely starts with the config root is not trusted."""
    config_root = Path(hass.config.path())
    outside = config_root.with_name(f"{config_root.name}-secret") / "art.png"

    with pytest.raises(ServiceValidationError, match="outside the allowed"):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": str(outside)},
            blocking=True,
        )

    upload_service.async_upload_image.assert_not_awaited()


async def test_upload_art_returns_real_tv_content_id(hass, upload_service):
    """Callers can request the exact content ID as a service response."""
    upload_service.async_upload_image.return_value = "MY-CONTENT-123"

    with (
        patch("pathlib.Path.is_file", return_value=True),
        patch("pathlib.Path.open", mock_open(read_data=b"LOCALIMAGE")),
    ):
        response = await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "sunset.png", "tags": "morning"},
            blocking=True,
            return_response=True,
        )

    assert response == {
        "content_id": "MY-CONTENT-123",
        "content_ids": ["MY-CONTENT-123"],
    }


async def test_upload_art_tracks_tags_on_the_real_content_id(hass, upload_service):
    """Tracking inputs travel with the upload instead of a basename upsert."""
    upload_service.async_upload_image.return_value = "MY-CONTENT-123"

    with (
        patch("pathlib.Path.is_file", return_value=True),
        patch("pathlib.Path.open", mock_open(read_data=b"LOCALIMAGE")),
    ):
        await hass.services.async_call(
            DOMAIN,
            "upload_art",
            {"path": "sunset.png", "tags": "morning"},
            blocking=True,
        )

    upload_service.async_upload_image.assert_awaited_once_with(
        b"LOCALIMAGE",
        matte="none",
        source_file="sunset.png",
        tags="morning",
    )
    upload_service.async_track_art.assert_not_awaited()


async def test_delete_art_reports_a_rejected_identifier(hass, upload_service):
    """The public action clearly rejects an untracked or path-like identifier."""
    upload_service.async_delete_art.return_value = False

    with pytest.raises(ServiceValidationError, match="tracked local artwork"):
        await hass.services.async_call(
            DOMAIN,
            "delete_art",
            {"content_id": "/config/configuration.yaml"},
            blocking=True,
        )


async def test_power_key_wake_requires_explicit_off_status(hass, upload_service):
    """An unknown status must not trigger the toggle-style POWER key."""
    upload_service.async_get_artmode_status.return_value = None

    await hass.services.async_call(
        DOMAIN,
        "set_artmode",
        {"enabled": True},
        blocking=True,
    )

    upload_service.async_set_artmode.assert_awaited_once_with(True)
    upload_service.async_send_key.assert_not_awaited()


async def test_power_key_wakes_tv_after_explicit_off_status(hass, upload_service):
    """An explicitly off TV gets POWER and Art Mode is reasserted."""
    upload_service.async_get_artmode_status.return_value = "off"

    with patch(
        "custom_components.samsung_frame_art_director.asyncio.sleep",
        AsyncMock(),
    ):
        await hass.services.async_call(
            DOMAIN,
            "set_artmode",
            {"enabled": True},
            blocking=True,
        )

    assert upload_service.async_set_artmode.await_args_list == [
        call(True),
        call(True),
    ]
    upload_service.async_send_key.assert_awaited_once_with("KEY_POWER")
