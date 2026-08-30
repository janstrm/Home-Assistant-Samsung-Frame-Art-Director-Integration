"""Tests for the isolated Samsung IP Control protocol client."""

import asyncio
import json
import ssl
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from custom_components.samsung_frame_art_director.ip_control import (
    MAX_RESPONSE_BYTES,
    IPControlAuthError,
    IPControlProtocolError,
    IPControlTransportError,
    IPControlUnavailableError,
    SamsungIPControlClient,
)


class _ExecutorHass:
    """Minimal Home Assistant executor boundary for protocol unit tests."""

    async def async_add_executor_job(self, target, *args):
        return await asyncio.to_thread(target, *args)


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self._content_length = content_length

    def getheader(self, name: str):
        if name.lower() == "content-length":
            return (
                str(self._content_length)
                if self._content_length is not None
                else None
            )
        return None

    def read(self, _amount: int) -> bytes:
        return self._body


def _client(token: str | None = "SECRET") -> SamsungIPControlClient:
    return SamsungIPControlClient(
        _ExecutorHass(),
        "frame.local",
        token=token,
        port=1516,
    )


async def test_pair_omits_params_and_returns_access_token():
    """Pairing is the only request without token-bearing params."""
    client = _client(token=None)
    client._sync_post = MagicMock(
        return_value=b'{"jsonrpc":"2.0","id":1,"result":{"AccessToken":"NEW"}}'
    )

    assert await client.async_pair() == "NEW"

    payload = json.loads(client._sync_post.call_args.args[0])
    assert payload == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "createAccessToken",
    }

    client._sync_post.reset_mock()
    client._sync_post.return_value = b'{"result":{"power":"powerOn"}}'
    assert await client.async_get_power_state() == "powerOn"
    authenticated_payload = json.loads(client._sync_post.call_args.args[0])
    assert authenticated_payload["params"] == {"AccessToken": "NEW"}


@pytest.mark.parametrize(
    ("method_name", "expected_params", "expected_result"),
    [
        ("async_get_power_state", {"AccessToken": "SECRET"}, "powerOn"),
        (
            "async_power_on",
            {"AccessToken": "SECRET", "power": "powerOn"},
            "powerOn",
        ),
        (
            "async_power_off",
            {"AccessToken": "SECRET", "power": "powerOff"},
            "powerOff",
        ),
        (
            "async_reboot",
            {"AccessToken": "SECRET", "power": "reboot"},
            "reboot",
        ),
    ],
)
async def test_power_requests_use_exact_envelope(
    method_name, expected_params, expected_result
):
    """Power reads and writes use powerControl with token-in-params auth."""
    client = _client()
    client._sync_post = MagicMock(
        return_value=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"power": expected_result}}
        ).encode()
    )

    assert await getattr(client, method_name)() == expected_result

    payload = json.loads(client._sync_post.call_args.args[0])
    assert payload == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "powerControl",
        "params": expected_params,
    }


async def test_authenticated_request_requires_token_before_transport():
    client = _client(token=None)
    client._sync_post = MagicMock()

    with pytest.raises(IPControlAuthError, match="pairing required"):
        await client.async_get_power_state()

    client._sync_post.assert_not_called()


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        ({"error": {"code": -32010, "message": "Unauthorized"}}, IPControlAuthError),
        ({"code": -32700, "message": "Parse error"}, IPControlAuthError),
        (
            {"error": {"code": -32601, "message": "Method not found"}},
            IPControlUnavailableError,
        ),
        (
            {"error": {"code": -32002, "message": "Unavailable now"}},
            IPControlUnavailableError,
        ),
        ({"error": {"code": -32099, "message": "Other"}}, IPControlProtocolError),
    ],
)
async def test_protocol_errors_are_classified(response, error_type):
    client = _client()
    client._sync_post = MagicMock(return_value=json.dumps(response).encode())

    with pytest.raises(error_type):
        await client.async_get_power_state()


@pytest.mark.parametrize(
    "response",
    [
        b"not json SECRET",
        b'[{"result":{}}]',
        b'{"jsonrpc":"2.0","id":1}',
        b'{"jsonrpc":"2.0","id":1,"result":[]}',
    ],
)
async def test_invalid_response_is_rejected_without_leaking_token(response):
    client = _client()
    client._sync_post = MagicMock(return_value=response)

    with pytest.raises(IPControlProtocolError) as err:
        await client.async_get_power_state()

    assert "SECRET" not in str(err.value)
    assert "SECRET" not in repr(client)


@pytest.mark.parametrize(
    "power_result",
    [{}, {"power": None}, {"power": 1}, {"power": ""}],
)
async def test_invalid_power_result_is_rejected(power_result):
    client = _client()
    client._sync_post = MagicMock(
        return_value=json.dumps({"result": power_result}).encode()
    )

    with pytest.raises(IPControlProtocolError, match="valid power value"):
        await client.async_get_power_state()


def test_tls_context_accepts_only_the_tv_self_signed_certificate_boundary():
    context = _client()._build_ssl_context()

    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE


def test_transport_posts_json_with_bounded_read_and_closes_connection():
    client = _client()
    response = _Response(b'{"result":{}}')
    connection = MagicMock()
    connection.getresponse.return_value = response

    with patch(
        "custom_components.samsung_frame_art_director.ip_control.http.client.HTTPSConnection",
        return_value=connection,
    ) as connection_factory:
        assert client._sync_post(b"{}", 5) == b'{"result":{}}'

    connection_factory.assert_called_once()
    connection.request.assert_called_once_with(
        "POST",
        "/",
        body=b"{}",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    connection.close.assert_called_once_with()


@pytest.mark.parametrize(
    "response",
    [
        _Response(b"{}", content_length=MAX_RESPONSE_BYTES + 1),
        _Response(b"x" * (MAX_RESPONSE_BYTES + 1)),
    ],
)
def test_transport_rejects_declared_or_streamed_oversized_response(response):
    client = _client()
    connection = MagicMock()
    connection.getresponse.return_value = response

    with (
        patch(
            "custom_components.samsung_frame_art_director.ip_control.http.client.HTTPSConnection",
            return_value=connection,
        ),
        pytest.raises(IPControlProtocolError, match="response size limit"),
    ):
        client._sync_post(b"{}", 5)

    connection.close.assert_called_once_with()


def test_transport_timeout_is_classified_and_connection_is_closed():
    client = _client()
    connection = MagicMock()
    connection.request.side_effect = TimeoutError

    with (
        patch(
            "custom_components.samsung_frame_art_director.ip_control.http.client.HTTPSConnection",
            return_value=connection,
        ),
        pytest.raises(IPControlTransportError),
    ):
        client._sync_post(b"{}", 5)

    connection.close.assert_called_once_with()


async def test_aggregate_timeout_quarantines_worker_after_prompt_error():
    """Timeout returns promptly while the host lock quarantines the worker."""
    client = _client()
    host_lock = asyncio.Lock()
    release_worker = threading.Event()
    worker_finished = threading.Event()

    def slow_request():
        release_worker.wait(timeout=1)
        worker_finished.set()
        return {}

    started = time.monotonic()
    with pytest.raises(IPControlTransportError, match="timed out"):
        await client._async_run_blocking_contained(
            slow_request,
            aggregate_timeout=0.001,
            lock=host_lock,
        )

    assert time.monotonic() - started < 0.1
    assert host_lock.locked()
    with pytest.raises(IPControlTransportError, match="timed out"):
        await client._async_run_blocking_contained(
            lambda: {},
            aggregate_timeout=0.001,
            lock=host_lock,
        )
    assert host_lock.locked()

    release_worker.set()
    for _ in range(20):
        if not host_lock.locked():
            break
        await asyncio.sleep(0.01)

    assert worker_finished.is_set()
    assert not host_lock.locked()
    assert await client._async_run_blocking_contained(
        lambda: {},
        aggregate_timeout=0.1,
        lock=host_lock,
    ) == {}


async def test_clients_for_same_host_serialize_requests():
    """Two clients cannot overlap the TV's fragile port-1516 connection."""
    first = _client()
    second = _client()
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def request(*_args):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with state_lock:
            active -= 1
        return {"power": "powerOn"}

    first._sync_request = request
    second._sync_request = request

    await asyncio.gather(
        first.async_get_power_state(),
        second.async_get_power_state(),
    )

    assert max_active == 1
