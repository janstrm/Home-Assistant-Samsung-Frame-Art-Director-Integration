"""Tests for device discovery and pairing boundaries."""

import sys
from types import ModuleType
from unittest.mock import patch

from custom_components.samsung_frame_art_director.bridge import async_probe_device_info


async def test_probe_device_info_uses_rest_without_opening_websocket():
    """Discovery reads REST device info without constructing a WS client."""
    rest_calls = []
    expected_info = {"device": {"modelName": "GQ65LS03D", "name": "The Frame"}}

    class ForbiddenSamsungTVWS:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("device-info discovery must not construct SamsungTVWS")

    class FakeSamsungTVRest:
        def __init__(self, host, port, timeout):
            rest_calls.append((host, port, timeout))

        def rest_device_info(self):
            return expected_info

    fake_samsungtvws = ModuleType("samsungtvws")
    fake_samsungtvws.SamsungTVWS = ForbiddenSamsungTVWS
    fake_rest = ModuleType("samsungtvws.rest")
    fake_rest.SamsungTVRest = FakeSamsungTVRest

    with patch.dict(
        sys.modules,
        {
            "samsungtvws": fake_samsungtvws,
            "samsungtvws.rest": fake_rest,
        },
    ):
        result = await async_probe_device_info("192.0.2.10")

    assert result == (8002, expected_info)
    assert rest_calls == [("192.0.2.10", 8002, 10)]
