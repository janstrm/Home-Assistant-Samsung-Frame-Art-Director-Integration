"""Art profile negotiation and socket-level startup regression coverage."""

import asyncio
import json
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import websocket
from samsungtvws import SamsungTVWS

from custom_components.samsung_frame_art_director.api import (
    DeviceUnavailableError,
    SamsungFrameClient,
)
from custom_components.samsung_frame_art_director.art_connection import (
    PROFILES,
    ArtHandshakeError,
    ManagedArt,
)


class Socket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.closed = 0
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self):
        frame = next(self.frames)
        if isinstance(frame, Exception):
            raise frame
        return json.dumps(frame)

    def close(self):
        self.closed += 1


def event(name, **data):
    return {"event": f"ms.channel.{name}", "data": data}


@pytest.mark.parametrize("port,mode", PROFILES)
def test_native_child_uses_profile_without_exposing_tokens(port, mode, caplog):
    caplog.set_level(logging.DEBUG)
    client = SamsungFrameClient(None, "frame.local", "REMOTE-SECRET", port=8002, art_port=port, art_auth_mode=mode)
    parent = SamsungTVWS("frame.local", port=8002, token="REMOTE-SECRET", timeout=10)
    art = client._make_art(parent)
    assert isinstance(art, ManagedArt)
    sock = Socket([event("connect", token="ART-SECRET"), event("ready")])
    with patch("custom_components.samsung_frame_art_director.art_connection.websocket.create_connection", return_value=sock) as create:
        assert art.open() is sock
        assert art.open() is sock  # already-ready sockets never wait for ready again
    url = urlsplit(create.call_args.args[0])
    assert url.port == port
    assert url.scheme == ("wss" if port == 8002 else "ws")
    assert parse_qs(url.query).get("token") == (["REMOTE-SECRET"] if mode == "saved_remote_token" else None)
    client._close_art_connection(parent, art)
    assert sock.closed == 1
    assert client.token == "REMOTE-SECRET"
    assert "REMOTE-SECRET" not in caplog.text
    assert "ART-SECRET" not in caplog.text


@pytest.mark.parametrize("prefix,phase", [([], "connect"), ([event("connect")], "ready")])
@pytest.mark.parametrize(
    "failure",
    [
        websocket.WebSocketTimeoutException("SECRET"),
        event("unauthorized", token="SECRET"),
        event("clientDisconnect", token="SECRET"),
        {"event": "SECRET", "data": "SECRET"},
    ],
)
def test_failed_handshake_closes_socket_and_sanitizes(prefix, phase, failure, caplog):
    caplog.set_level(logging.DEBUG)
    art = ManagedArt("frame.local", port=8002, token="SECRET", timeout=10)
    sock = Socket([*prefix, failure])
    with patch("custom_components.samsung_frame_art_director.art_connection.websocket.create_connection", return_value=sock):
        with pytest.raises(ArtHandshakeError) as caught:
            art.open()
    assert caught.value.phase == phase
    assert sock.closed == 1
    assert art.connection is None
    assert "SECRET" not in str(caught.value)
    assert "SECRET" not in caplog.text


def test_irrelevant_events_cannot_extend_handshake_deadline():
    art = ManagedArt("frame.local", timeout=10)
    art.handshake_timeout = 1
    sock = Socket([{"event": "ed.edenTV.update"}])
    with (
        patch("custom_components.samsung_frame_art_director.art_connection.websocket.create_connection", return_value=sock),
        patch("custom_components.samsung_frame_art_director.art_connection.time.monotonic", side_effect=[0, 0.5, 0.6, 1.1]),
        pytest.raises(ArtHandshakeError, match="timeout"),
    ):
        art.open()
    assert sock.closed == 1


def fake_remote(on_open, calls, closed):
    class Art:
        token = None
        token_file = None

        def open(self):
            profile = (self.port, "saved_remote_token" if self.token else "tokenless")
            calls.append(profile)
            on_open(profile)

        def close(self):
            closed.append(self.port)

        def get_brightness(self):
            self.open()
            return 5

    return SimpleNamespace(
        token="REMOTE",
        open=lambda: None,
        art=Art,
        close=lambda: None,
        rest_device_info=lambda: {"device": {"duid": "uuid:frame"}},
    )


@pytest.mark.parametrize("working", PROFILES)
async def test_profile_survives_reconstructed_client_and_is_used_by_commands(hass, working):
    calls, closed = [], []

    def accept(profile):
        if profile != working:
            raise ArtHandshakeError("connect", "timeout", retryable=True)

    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002)
    remote = fake_remote(accept, calls, closed)
    with patch.object(client, "_make_tv", return_value=remote):
        await client.async_connect_and_pair()
    assert client.art_profile == working
    assert calls == list(PROFILES[: PROFILES.index(working) + 1])
    assert len(calls) == len(closed)
    calls.clear()
    restarted = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002, art_port=working[0], art_auth_mode=working[1])
    with patch.object(restarted, "_make_tv", return_value=remote):
        await restarted.async_connect_and_pair()
        assert await restarted.async_get_brightness() == 5
    assert calls == [working, working]
    assert restarted._port == 8002
    assert restarted.token == "REMOTE"


async def test_stale_profile_replaced_only_by_complete_success(hass):
    calls, closed = [], []

    def accept(profile):
        if profile != PROFILES[1]:
            raise ArtHandshakeError("ready", "timeout", retryable=True)

    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002, art_port=8001, art_auth_mode="tokenless")
    with patch.object(client, "_make_tv", return_value=fake_remote(accept, calls, closed)):
        await client.async_connect_and_pair()
    assert calls == [PROFILES[2], PROFILES[0], PROFILES[1]]
    assert client.art_profile == PROFILES[1]


async def test_art_rejection_does_not_reauthenticate_remote_or_erase_profile(hass):
    def reject(profile):
        raise ArtHandshakeError("connect", "ms.channel.unauthorized", retryable=True)

    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002, art_port=8001, art_auth_mode="tokenless")
    calls = []
    with patch.object(client, "_make_tv", return_value=fake_remote(reject, calls, [])):
        with pytest.raises(DeviceUnavailableError):
            await client.async_connect_and_pair()
    assert client.art_profile == PROFILES[2]
    assert client.token == "REMOTE"
    assert len(calls) == 3
    assert not client.is_connected


async def test_elapsed_timeout_then_success_has_its_own_budget(hass):
    def accept(profile):
        if profile == PROFILES[0]:
            time.sleep(0.06)
            raise TimeoutError

    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002)
    with (
        patch.object(client, "_make_tv", return_value=fake_remote(accept, [], [])),
        patch("custom_components.samsung_frame_art_director.api.CONNECTION_ATTEMPT_TIMEOUT_SECONDS", 0.05),
    ):
        await client.async_connect_and_pair()
    assert client.art_profile == PROFILES[1]
    assert client.is_connected


async def test_cancelled_candidate_is_drained_closed_and_not_published(hass):
    started, finish = threading.Event(), threading.Event()
    closed = []

    def accept(profile):
        started.set()
        assert finish.wait(2)

    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002)
    with patch.object(client, "_make_tv", return_value=fake_remote(accept, [], closed)):
        task = asyncio.create_task(client.async_connect_and_pair())
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed == [8002]
    assert client.art_profile is None
    assert not client.is_connected


async def test_wrapped_timeout_can_fall_back(hass):
    def accept(profile):
        if profile == PROFILES[0]:
            try:
                raise websocket.WebSocketTimeoutException("private response")
            except websocket.WebSocketTimeoutException as err:
                raise type("ConnectionFailure", (Exception,), {})("wrapped") from err

    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002)
    with patch.object(client, "_make_tv", return_value=fake_remote(accept, [], [])):
        await client.async_connect_and_pair()
    assert client.art_profile == PROFILES[1]


async def test_refused_saved_port_tries_other_port_without_repeating_auth_modes(hass):
    calls = []

    def accept(profile):
        if profile[0] == 8002:
            raise ArtHandshakeError("connect", "connection_refused", retryable=True)

    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002, art_port=8002, art_auth_mode="saved_remote_token")
    with patch.object(client, "_make_tv", return_value=fake_remote(accept, calls, [])):
        await client.async_connect_and_pair()
    assert calls == [(8002, "saved_remote_token"), (8001, "tokenless")]
    assert client.art_profile == PROFILES[2]


def test_refused_socket_is_a_port_failure():
    art = ManagedArt("frame.local", timeout=10)
    with (
        patch(
            "custom_components.samsung_frame_art_director.art_connection.websocket.create_connection",
            side_effect=ConnectionRefusedError("SECRET"),
        ),
        pytest.raises(ArtHandshakeError) as caught,
    ):
        art.open()
    assert caught.value.event == "connection_refused"
    assert caught.value.retryable
    assert art.connection is None


async def test_remote_rotation_is_retained_without_accepting_art_token(hass):
    remote = fake_remote(lambda profile: None, [], [])
    remote.open = lambda: setattr(remote, "token", "REMOTE-ROTATED")
    original_art = remote.art

    def art_factory():
        art = original_art()
        art.open = lambda: setattr(art, "token", "ART-ONLY")
        return art

    remote.art = art_factory
    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002)
    persisted = []
    client.set_token_persister(persisted.append)
    with patch.object(client, "_make_tv", return_value=remote):
        await client.async_connect_and_pair()
    assert persisted == ["REMOTE-ROTATED"]
    assert client.token == "REMOTE-ROTATED"


async def test_cancellation_during_remote_cleanup_does_not_publish(hass):
    started, finish = threading.Event(), threading.Event()
    remote = fake_remote(lambda profile: None, [], [])

    def close():
        started.set()
        assert finish.wait(2)

    remote.close = close
    client = SamsungFrameClient(hass, "frame.local", "REMOTE", port=8002)
    with patch.object(client, "_make_tv", return_value=remote):
        task = asyncio.create_task(client.async_connect_and_pair())
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert client.art_profile is None
    assert not client.is_connected
