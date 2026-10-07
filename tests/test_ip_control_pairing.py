"""Behavior tests for user-initiated IP Control pairing."""

from unittest.mock import AsyncMock, call, patch, sentinel

import pytest

from custom_components.samsung_frame_art_director.ip_control import (
    IPControlAuthError,
    IPControlProtocolError,
    IPControlTransportError,
    IPControlUnavailableError,
)
from custom_components.samsung_frame_art_director.ip_control_pairing import (
    async_pair_ip_control,
)

_PAIRING = "custom_components.samsung_frame_art_director.ip_control_pairing"


async def test_pairing_falls_back_to_legacy_port_after_transport_failure():
    current = AsyncMock()
    current.async_pair.side_effect = IPControlTransportError("unreachable")
    legacy = AsyncMock()
    legacy.async_pair.return_value = "TOKEN"

    with patch(
        f"{_PAIRING}.SamsungIPControlClient", side_effect=[current, legacy]
    ) as client_type:
        result = await async_pair_ip_control(sentinel.hass, "frame.local")

    assert result == ("TOKEN", 1515)
    assert client_type.call_args_list == [
        call(sentinel.hass, "frame.local", port=1516),
        call(sentinel.hass, "frame.local", port=1515),
    ]


@pytest.mark.parametrize(
    "error",
    [
        IPControlAuthError("rejected"),
        IPControlUnavailableError("wrong state"),
        IPControlProtocolError("bad response"),
    ],
)
async def test_pairing_does_not_fallback_after_tv_response(error):
    current = AsyncMock()
    current.async_pair.side_effect = error

    with patch(
        f"{_PAIRING}.SamsungIPControlClient", return_value=current
    ) as client_type:
        with pytest.raises(type(error), match=str(error)):
            await async_pair_ip_control(sentinel.hass, "frame.local")

    client_type.assert_called_once()


@pytest.mark.parametrize("cause, reason", [(ConnectionRefusedError("PRIVATE"), "connection_refused"), (TimeoutError("PRIVATE"), "timeout")])
async def test_pairing_reports_each_failed_endpoint_without_raw_errors(caplog, cause, reason):
    error = IPControlTransportError("PRIVATE response")
    error.__cause__ = cause
    client = AsyncMock()
    client.async_pair.side_effect = error
    with patch(f"{_PAIRING}.SamsungIPControlClient", return_value=client):
        with pytest.raises(IPControlTransportError):
            await async_pair_ip_control(sentinel.hass, "frame.local")
    assert "port=1516" in caplog.text
    assert "port=1515" in caplog.text
    assert f"reason={reason}" in caplog.text
    assert "PRIVATE" not in caplog.text
