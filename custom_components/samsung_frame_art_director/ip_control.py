"""Samsung IP Control HTTPS/JSON-RPC client.

This boundary is intentionally independent from the Art/WebSocket client. It
contains protocol and transport behavior only; config-entry persistence and
Home Assistant actions are layered on in separate packages.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import ssl
from contextlib import suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

DEFAULT_IP_CONTROL_PORT = 1516
COMMAND_TIMEOUT_SECONDS = 5
PAIRING_TIMEOUT_SECONDS = 45
MAX_RESPONSE_BYTES = 256 * 1024

ERROR_UNAUTHORIZED = -32010
ERROR_PARSE_STALE_TOKEN = -32700
ERROR_UNAVAILABLE = -32002
ERROR_METHOD_NOT_FOUND = -32601

_HOST_LOCKS: dict[tuple[int, str, int], asyncio.Lock] = {}
_DRAIN_TASKS: set[asyncio.Task[None]] = set()


class IPControlError(Exception):
    """Base error for Samsung IP Control."""


class IPControlAuthError(IPControlError):
    """The access token is absent, stale, or rejected."""


class IPControlUnavailableError(IPControlError):
    """The method is unavailable on this model or in its current state."""


class IPControlProtocolError(IPControlError):
    """The TV returned an invalid or unexpected protocol response."""


class IPControlTransportError(IPControlError):
    """The IP Control endpoint could not complete the request."""


def _host_lock(host: str, port: int) -> asyncio.Lock:
    """Return one serialization lock per event loop and TV endpoint."""
    loop_key = id(asyncio.get_running_loop())
    key = (loop_key, host, port)
    if (lock := _HOST_LOCKS.get(key)) is None:
        lock = _HOST_LOCKS[key] = asyncio.Lock()
    return lock


class SamsungIPControlClient:
    """Small async facade over Samsung's blocking IP Control endpoint."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        *,
        token: str | None = None,
        port: int = DEFAULT_IP_CONTROL_PORT,
    ) -> None:
        self._hass = hass
        self._host = host
        self._port = port
        self._token = token

    def __repr__(self) -> str:
        """Return diagnostics-safe identity without credential material."""
        return (
            f"{type(self).__name__}(host={self._host!r}, port={self._port!r}, "
            f"paired={bool(self._token)!r})"
        )

    async def async_pair(self) -> str:
        """Wait for on-TV approval and return the newly issued access token."""
        result = await self._async_request(
            "createAccessToken",
            include_token=False,
            timeout=PAIRING_TIMEOUT_SECONDS,
        )
        token = result.get("AccessToken")
        if not isinstance(token, str) or not token:
            raise IPControlProtocolError(
                "IP Control pairing response did not contain an access token"
            )
        self._token = token
        return token

    async def async_get_power_state(self) -> str:
        """Return the power value reported by powerControl."""
        result = await self._async_request("powerControl")
        return self._power_result(result)

    async def async_power_on(self) -> str:
        """Request real panel power on."""
        result = await self._async_request(
            "powerControl", {"power": "powerOn"}
        )
        return self._power_result(result)

    async def async_power_off(self) -> str:
        """Request real panel power off."""
        result = await self._async_request(
            "powerControl", {"power": "powerOff"}
        )
        return self._power_result(result)

    async def async_reboot(self) -> str:
        """Request a panel reboot."""
        result = await self._async_request(
            "powerControl", {"power": "reboot"}
        )
        return self._power_result(result)

    @staticmethod
    def _power_result(result: dict[str, Any]) -> str:
        power = result.get("power")
        if not isinstance(power, str) or not power:
            raise IPControlProtocolError(
                "IP Control response did not contain a valid power value"
            )
        return power

    async def _async_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        include_token: bool = True,
        timeout: int = COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Serialize one request per TV and contain its blocking worker."""
        return await self._async_run_blocking_contained(
            self._sync_request,
            method,
            params,
            include_token,
            timeout,
            aggregate_timeout=timeout + 1,
            lock=_host_lock(self._host, self._port),
        )

    async def _async_run_blocking_contained(
        self,
        target: Callable[..., dict[str, Any]],
        *args: Any,
        aggregate_timeout: float,
        lock: asyncio.Lock,
    ) -> dict[str, Any]:
        """Bound caller time while quarantining a late worker behind its lock."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + aggregate_timeout
        try:
            await asyncio.wait_for(
                lock.acquire(), timeout=aggregate_timeout
            )
        except TimeoutError as err:
            raise IPControlTransportError("IP Control request timed out") from err

        worker = asyncio.ensure_future(
            self._hass.async_add_executor_job(target, *args)
        )
        release_lock = True
        try:
            remaining = max(0.0, deadline - loop.time())
            return await asyncio.wait_for(
                asyncio.shield(worker), timeout=remaining
            )
        except TimeoutError as err:
            release_lock = False
            self._schedule_worker_drain(worker, lock)
            raise IPControlTransportError("IP Control request timed out") from err
        except asyncio.CancelledError:
            release_lock = False
            self._schedule_worker_drain(worker, lock)
            raise
        finally:
            if release_lock:
                lock.release()

    @staticmethod
    def _schedule_worker_drain(
        worker: asyncio.Future[dict[str, Any]], lock: asyncio.Lock
    ) -> None:
        """Keep the endpoint quarantined until a timed-out worker exits."""
        async def _drain() -> None:
            try:
                await worker
            except (Exception, asyncio.CancelledError):  # noqa: BLE001
                pass
            finally:
                lock.release()

        task = asyncio.create_task(_drain())
        _DRAIN_TASKS.add(task)
        task.add_done_callback(_DRAIN_TASKS.discard)

    def _sync_request(
        self,
        method: str,
        params: dict[str, Any] | None,
        include_token: bool,
        timeout: int,
    ) -> dict[str, Any]:
        """Build, send, and validate one JSON-RPC request in an executor."""
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
        }
        if include_token:
            if not self._token:
                raise IPControlAuthError(
                    "IP Control is not paired; pairing required"
                )
            request["params"] = {
                "AccessToken": self._token,
                **(params or {}),
            }
        elif params:
            request["params"] = params

        response = self._sync_post(
            json.dumps(request, separators=(",", ":")).encode("utf-8"),
            timeout,
        )
        try:
            decoded = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise IPControlProtocolError(
                "IP Control returned an invalid JSON response"
            ) from err
        if not isinstance(decoded, dict):
            raise IPControlProtocolError(
                "IP Control returned a non-object response"
            )

        error = decoded.get("error")
        if error is None and "code" in decoded and "result" not in decoded:
            error = {
                "code": decoded.get("code"),
                "message": decoded.get("message"),
            }
        if error is not None:
            self._raise_protocol_error(error, include_token=include_token)

        result = decoded.get("result")
        if not isinstance(result, dict):
            raise IPControlProtocolError(
                "IP Control response did not contain an object result"
            )
        return result

    @staticmethod
    def _raise_protocol_error(error: Any, *, include_token: bool) -> None:
        """Map observed Samsung error envelopes without exposing payloads."""
        code = error.get("code") if isinstance(error, dict) else None
        if code == ERROR_UNAUTHORIZED or (
            code == ERROR_PARSE_STALE_TOKEN and include_token
        ):
            raise IPControlAuthError("IP Control access token was rejected")
        if code in (ERROR_UNAVAILABLE, ERROR_METHOD_NOT_FOUND):
            raise IPControlUnavailableError(
                "IP Control method is unavailable on this TV or in its current state"
            )
        raise IPControlProtocolError(
            f"IP Control returned JSON-RPC error code {code!r}"
        )

    @staticmethod
    def _build_ssl_context() -> ssl.SSLContext:
        """Build the local-TV TLS context for its self-signed certificate."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _sync_post(self, payload: bytes, timeout: int) -> bytes:
        """POST one bounded request and close its short-lived connection."""
        connection: http.client.HTTPSConnection | None = None
        try:
            connection = http.client.HTTPSConnection(
                self._host,
                self._port,
                timeout=timeout,
                context=self._build_ssl_context(),
            )
            connection.request(
                "POST",
                "/",
                body=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise IPControlProtocolError(
                    f"IP Control returned HTTP status {response.status}"
                )

            content_length = response.getheader("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError as err:
                    raise IPControlProtocolError(
                        "IP Control returned an invalid Content-Length"
                    ) from err
                if declared_length > MAX_RESPONSE_BYTES:
                    raise IPControlProtocolError(
                        "IP Control response size limit exceeded"
                    )

            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise IPControlProtocolError(
                    "IP Control response size limit exceeded"
                )
            return body
        except IPControlError:
            raise
        except (TimeoutError, OSError, ssl.SSLError, http.client.HTTPException) as err:
            raise IPControlTransportError(
                f"IP Control transport failed for {self._host}:{self._port}"
            ) from err
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()
