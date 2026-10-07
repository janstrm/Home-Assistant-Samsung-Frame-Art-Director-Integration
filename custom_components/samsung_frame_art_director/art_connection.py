"""Bounded Art handshakes with explicit socket ownership and safe errors."""

from __future__ import annotations

import json
import logging
import ssl
import time

import websocket
from samsungtvws.art import SamsungTVArt

_LOGGER = logging.getLogger(__name__)
# This beta first tests the saved Remote credential on Art/8002. A remembered
# working profile still wins, and tokenless devices retain bounded fallback.
PROFILES = ((8002, "saved_remote_token"), (8002, "tokenless"), (8001, "tokenless"))
_STARTUP_EVENTS = {"ed.edenTV.update", "ms.voiceApp.hide"}
_EVENTS = _STARTUP_EVENTS | {
    "ms.channel.connect",
    "ms.channel.ready",
    "ms.channel.unauthorized",
    "ms.channel.timeOut",
    "ms.channel.clientDisconnect",
    "ms.channel.clientConnect",
}


class ArtHandshakeError(ConnectionError):
    """An Art-only failure; never a rejection of Remote credentials."""

    def __init__(self, phase: str, event: str, *, retryable: bool = False):
        self.phase = phase
        self.event = event
        self.retryable = retryable
        super().__init__(f"Art handshake failed: phase={phase} event={event}")


class ManagedArt(SamsungTVArt):
    """Use upstream Art commands with a locally owned, deadline-bound handshake."""

    handshake_timeout: float = 10
    auth_mode: str = "tokenless"

    def open(self):
        if self.connection is not None:
            return self.connection
        started = time.monotonic()
        deadline = started + self.handshake_timeout
        phase = "connect"
        try:
            # Assign immediately: even a failure before channel.connect is owned.
            self.connection = websocket.create_connection(
                self._format_websocket_url(self.endpoint),
                timeout=self.handshake_timeout,
                sslopt={"cert_reqs": ssl.CERT_NONE} if self.port == 8002 else {},
                connection="Connection: Upgrade",
            )
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                self.connection.settimeout(remaining)
                response = json.loads(self.connection.recv())
                if not isinstance(response, dict):
                    raise ValueError
                event = response.get("event")
                if event not in _EVENTS:
                    event = "unexpected"
                _LOGGER.debug(
                    "Art handshake port=%s auth_mode=%s phase=%s event=%s elapsed=%.3f",
                    self.port,
                    self.auth_mode,
                    phase,
                    event,
                    time.monotonic() - started,
                )
                if event in _STARTUP_EVENTS:
                    continue
                if phase == "connect" and event == "ms.channel.connect":
                    # Art-issued tokens never change Remote authentication.
                    phase = "ready"
                    continue
                if phase == "ready" and event == "ms.channel.ready":
                    self.connection.settimeout(self.timeout)
                    return self.connection
                raise ArtHandshakeError(
                    phase,
                    event,
                    retryable=event in {"ms.channel.timeOut", "ms.channel.unauthorized"},
                )
        except ArtHandshakeError:
            self.close()
            raise
        except (TimeoutError, websocket.WebSocketTimeoutException):
            self.close()
            raise ArtHandshakeError(phase, "timeout", retryable=True) from None
        except ConnectionRefusedError:
            self.close()
            raise ArtHandshakeError(phase, "connection_refused", retryable=True) from None
        except Exception:  # noqa: BLE001
            self.close()
            # Upstream exceptions can contain URLs, tokens or whole TV frames.
            raise ArtHandshakeError(phase, "transport_or_protocol_error") from None

    def close(self):
        connection, self.connection = self.connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass

    def _websocket_event(self, event, response):
        """Do not forward token-bearing response bodies to upstream logging."""
        _LOGGER.debug("Art event=%s", event if event in _EVENTS else "other")


def managed_art(art, *, timeout: float, auth_mode: str):
    """Adapt native SDK children; alternate client implementations keep their API."""
    if isinstance(art, SamsungTVArt):
        art = ManagedArt(
            art.host,
            token=art.token,
            token_file=None,
            port=art.port,
            timeout=art.timeout,
            key_press_delay=art.key_press_delay,
            name=art.name,
        )
        art.handshake_timeout = timeout
        art.auth_mode = auth_mode
    return art
