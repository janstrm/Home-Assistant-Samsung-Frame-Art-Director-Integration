"""Tests for device discovery and pairing boundaries."""

import sys
from types import ModuleType
from unittest.mock import patch

from custom_components.samsung_frame_art_director.bridge import (
    async_probe_device_info,
    async_try_connect,
)
from custom_components.samsung_frame_art_director.const import RESULT_SUCCESS


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


async def test_sync_pairing_fallback_reuses_and_closes_one_art_child():
    """The fallback pairing probe owns one Art child for one parent client."""
    art_clients = []
    tv_clients = []
    art_calls = 0

    class FakeArt:
        token = "SAVED"

        def __init__(self):
            self.close_calls = 0
            art_clients.append(self)

        def supported(self):
            return True

        def get_artmode(self):
            return "on"

        def available(self):
            return []

        def close(self):
            self.close_calls += 1

    class FakeSamsungTVWS:
        def __init__(self, *_args, token=None, **_kwargs):
            self.token = token
            self.close_calls = 0
            tv_clients.append(self)

        def art(self):
            nonlocal art_calls
            art_calls += 1
            return FakeArt()

        def rest_device_info(self):
            return {"device": {"duid": "uuid:frame"}}

        def close(self):
            self.close_calls += 1

    fake_samsungtvws = ModuleType("samsungtvws")
    fake_samsungtvws.SamsungTVWS = FakeSamsungTVWS

    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        result = await async_try_connect("frame.local", 8002, "SAVED")

    assert result.result == RESULT_SUCCESS
    assert art_calls == 1
    assert len(art_clients) == len(tv_clients) == 1
    assert art_clients[0].close_calls == 1
    assert tv_clients[0].close_calls == 1
