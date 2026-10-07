"""Regressions found while investigating the field report in issue #51."""

import asyncio
import json
import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from samsungtvws.art import SamsungTVArt

from custom_components.samsung_frame_art_director import (
    _enable_verbose_logging,
    _reload_slideshow_timer,
)
from custom_components.samsung_frame_art_director.api import SamsungFrameClient
from custom_components.samsung_frame_art_director.const import DOMAIN
from custom_components.samsung_frame_art_director.number import SamsungFrameSlideshowInterval
from custom_components.samsung_frame_art_director.preview_deadline import bound_preview_reads
from custom_components.samsung_frame_art_director.runtime import SamsungFrameRuntimeData


@pytest.mark.parametrize("fails", [False, True])
async def test_concurrent_preview_requests_share_one_completed_fetch(hass, fails):
    """A dashboard refresh must not queue duplicate downloads ahead of controls."""
    started, release = threading.Event(), threading.Event()
    art = MagicMock()
    art.get_current.return_value = {"content_id": "MY-1"}

    def thumbnail(_content_id):
        started.set()
        assert release.wait(2)
        if fails:
            raise TimeoutError
        return b"preview"

    art.get_thumbnail.side_effect = thumbnail
    client = SamsungFrameClient(hass, "frame.local", "REMOTE")
    with patch.object(client, "_make_tv", return_value=SimpleNamespace(art=lambda: art, close=lambda: None)):
        first = asyncio.create_task(client.async_get_current_art())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            rest = [asyncio.create_task(client.async_get_current_art()) for _ in range(5)]
            await asyncio.sleep(0)
        finally:
            release.set()
        results = await asyncio.gather(first, *rest)

    assert all(result == {"content_id": "MY-1", "image": None if fails else b"preview"} for result in results)
    assert art.get_thumbnail.call_count == 1
    assert art.close.call_count == 1


async def test_slow_preview_cache_starts_when_fetch_finishes(hass):
    clock = [100.0]
    art = MagicMock()
    art.get_current.return_value = {"content_id": "MY-1"}

    def thumbnail(_content_id):
        clock[0] += 6
        return b"preview"

    art.get_thumbnail.side_effect = thumbnail
    client = SamsungFrameClient(hass, "frame.local", "REMOTE")
    with (
        patch.object(client, "_make_tv", return_value=SimpleNamespace(art=lambda: art, close=lambda: None)),
        patch("custom_components.samsung_frame_art_director.api.monotonic", side_effect=lambda: clock[0]),
    ):
        first = await client.async_get_current_art()
        assert await client.async_get_current_art() == first
        assert art.get_thumbnail.call_count == 1
        clock[0] += 6
        await client.async_get_current_art()
        assert art.get_thumbnail.call_count == 2


async def test_preview_timeout_does_not_start_another_download(hass):
    art = MagicMock()
    art.get_current.return_value = {"content_id": "MY-1"}
    art.get_thumbnail.side_effect = TimeoutError
    client = SamsungFrameClient(hass, "frame.local", "REMOTE")
    with patch.object(client, "_make_tv", return_value=SimpleNamespace(art=lambda: art, close=lambda: None)):
        result = await client.async_get_current_art()

    assert result == {"content_id": "MY-1", "image": None}
    art.get_preview.assert_not_called()
    art.get_photo.assert_not_called()
    art.close.assert_called_once()


@pytest.mark.parametrize("logger_name", ["samsungtvws.helper", "samsungtvws.connection", "samsungtvws.art.art"])
@pytest.mark.parametrize("payload", [
    '{"attributes":{"token":"FIELD-SECRET"}}',
    {"AccessToken": "FIELD-SECRET"},
    'wss://frame:8002/api?token=FIELD-SECRET',
    r'{"data":"{\"token\":\"FIELD-SECRET\"}"}',
    'New token FIELD-SECRET',
    'Got token FIELD-SECRET',
    'Save token to file: FIELD-SECRET',
])
def test_ha_debug_cannot_reenable_credential_payloads(caplog, logger_name, payload):
    _enable_verbose_logging()
    # Home Assistant's debug-log action can override dependency logger levels.
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        logging.getLogger(logger_name).debug("Processing API response: %s", payload)
    assert "FIELD-SECRET" not in caplog.text


def test_wrapped_tv_exception_is_safe_in_integration_logs(caplog):
    _enable_verbose_logging()
    logger = logging.getLogger("custom_components.samsung_frame_art_director.api")
    try:
        raise RuntimeError({"token": "EXCEPTION-SECRET"})
    except RuntimeError:
        logger.exception("Art request failed")
    assert "EXCEPTION-SECRET" not in caplog.text
    assert "Art request failed" in caplog.text


async def test_unrelated_tv_events_cannot_hold_preview_lock_indefinitely(hass):
    clock = [0.0]

    class ChattyArt:
        connection = MagicMock()
        token = None

        def get_current(self):
            return {"content_id": "SAM-1"}

        def _recv_frame(self):
            clock[0] += 1
            return "unrelated-event", {}

        def get_thumbnail(self, _content_id):
            # The upstream request matcher keeps reading unrelated events.
            for _ in range(100):
                self._recv_frame()
            pytest.fail("Preview was not stopped by its receive deadline")

        def close(self):
            pass

    art = ChattyArt()
    client = SamsungFrameClient(hass, "frame.local", "REMOTE")
    with (
        patch.object(client, "_make_tv", return_value=SimpleNamespace(art=lambda: art, close=lambda: None)),
        patch("custom_components.samsung_frame_art_director.preview_deadline.monotonic", side_effect=lambda: clock[0]),
    ):
        result = await client.async_get_current_art()
    assert result == {"content_id": "SAM-1", "image": None}
    assert clock[0] == 15
    assert not client._art_lock.locked()


def test_trickling_thumbnail_bytes_share_one_deadline():
    clock = [0.0]

    class SlowSocket:
        def settimeout(self, timeout):
            assert timeout > 0

        def recv(self, size):
            clock[0] += 1
            return b"x"

    class Art:
        def _recv_exact(self, sock, size):
            result = b""
            while len(result) < size:
                result += sock.recv(size - len(result))
            return result

    art = Art()
    with patch("custom_components.samsung_frame_art_director.preview_deadline.monotonic", side_effect=lambda: clock[0]):
        bound_preview_reads(art, 3)
        assert art._recv_exact(SlowSocket(), 2) == b"xx"
        with pytest.raises(TimeoutError):
            art._recv_exact(SlowSocket(), 10)
    assert clock[0] == 3


def test_native_sdk_thumbnail_wait_is_bounded_despite_unrelated_events():
    clock = [0.0]
    socket = MagicMock()

    def unrelated_frame():
        clock[0] += 1
        return json.dumps({"event": "d2d_service_message", "data": json.dumps({"event": "artmode_changed"})})

    socket.recv.side_effect = unrelated_frame
    art = SamsungTVArt("frame.local", timeout=3)
    art.connection = socket
    with (
        patch.object(art, "send_command"),
        patch("custom_components.samsung_frame_art_director.preview_deadline.monotonic", side_effect=lambda: clock[0]),
    ):
        bound_preview_reads(art, 3)
        with pytest.raises(TimeoutError):
            art.get_thumbnail("SAM-1")
    art.close()
    assert socket.recv.call_count == 3
    socket.close.assert_called_once()


async def test_zero_slideshow_interval_stays_zero_and_stops_timer(hass):
    entry = MockConfigEntry(domain=DOMAIN, options={"slideshow_enabled": True, "slideshow_interval": 5})
    entry.add_to_hass(hass)
    cancel = MagicMock()
    entry.runtime_data = SamsungFrameRuntimeData(client=MagicMock(), timer_unsub=cancel)
    entity = SamsungFrameSlideshowInterval(entry)
    entity.hass = hass
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_set_native_value(0)
    with patch("homeassistant.helpers.event.async_track_time_interval") as start:
        await _reload_slideshow_timer(hass, entry)
    assert entity.native_value == 0
    cancel.assert_called_once()
    start.assert_not_called()
    assert entry.runtime_data.timer_unsub is None
