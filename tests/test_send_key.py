"""Tests for SamsungFrameClient.async_send_key."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.samsung_frame_art_director.api import (
    ART_OPERATION_TIMEOUT_SECONDS,
    SamsungFrameClient,
)


async def test_send_key_holds_through_official_client_boundary():
    """A requested hold uses samsungtvws' Press/Sleep/Release command path."""
    client = SamsungFrameClient(SimpleNamespace(), "1.2.3.4", token="TOK")
    tv = MagicMock(spec=["hold_key", "close", "token"])
    tv.token = "TOK"
    client._make_tv = MagicMock(return_value=tv)
    client._async_run_blocking_contained = AsyncMock(
        side_effect=lambda operation, _timeout: operation()
    )

    await client.async_send_key("KEY_HDMI", hold_seconds=0.8)

    tv.hold_key.assert_called_once_with("KEY_HDMI", 0.8)
    client._make_tv.assert_called_once_with(timeout=ART_OPERATION_TIMEOUT_SECONDS)
    assert client._async_run_blocking_contained.await_args.args[1] == (
        2 * ART_OPERATION_TIMEOUT_SECONDS + 0.8
    )
    tv.close.assert_called_once()


async def test_send_key_uses_direct_send_key(hass):
    """The official samsungtvws client has send_key() and no remote()."""
    client = SamsungFrameClient(hass, "1.2.3.4", token="TOK")
    tv = MagicMock(spec=["send_key", "close", "token"])
    tv.token = "TOK"
    client._make_tv = MagicMock(return_value=tv)

    await client.async_send_key("KEY_HDMI")

    tv.send_key.assert_called_once_with("KEY_HDMI")
    tv.close.assert_called_once()


async def test_send_key_falls_back_to_remote_accessor(hass):
    """Forks that only expose remote().send_key() still work."""
    client = SamsungFrameClient(hass, "1.2.3.4", token="TOK")
    remote = MagicMock()
    tv = MagicMock(spec=["remote", "close", "token"])
    tv.token = "TOK"
    tv.remote.return_value = remote
    client._make_tv = MagicMock(return_value=tv)

    await client.async_send_key("KEY_POWER")

    remote.send_key.assert_called_once_with("KEY_POWER")
    tv.close.assert_called_once()


async def test_send_key_raises_when_no_sender_available(hass):
    """A client with neither method is a loud error, not a silent no-op."""
    client = SamsungFrameClient(hass, "1.2.3.4", token="TOK")
    tv = MagicMock(spec=["close", "token"])
    tv.token = "TOK"
    client._make_tv = MagicMock(return_value=tv)

    with pytest.raises(RuntimeError):
        await client.async_send_key("KEY_HDMI")
    tv.close.assert_called_once()
