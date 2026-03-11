"""Bridge layer for pairing/handshake and method/port selection.

Inspired by the official samsungtv integration, minimized for Frame Art Mode.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, Tuple
import logging

# Suppress local HTTPS cert warnings from TV endpoints during pairing/info calls
try:  # pragma: no cover - best-effort suppression
    import urllib3  # type: ignore

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

from .const import (
    CLIENT_NAME,
    RESULT_AUTH_MISSING,
    RESULT_CANNOT_CONNECT,
    RESULT_NOT_SUPPORTED,
    RESULT_SUCCESS,
    WEBSOCKET_PORTS,
    RESULT_INVALID_PIN,
    CONF_SESSION_ID,
)

_LOGGER = logging.getLogger(__name__)


class PairResult:
    """Simple container for pair attempt results."""

    def __init__(self, result: str, token: Optional[str] = None, info: Optional[dict[str, Any]] = None, session_id: Optional[str] = None) -> None:
        self.result = result
        self.token = token
        self.info = info or {}
        self.session_id = session_id


async def async_probe_device_info(host: str) -> Tuple[int | None, dict[str, Any] | None]:
    """Try to fetch device info to determine a working port.

    Returns (port, info) where port may be 8002 or 8001, or None if unreachable.
    """
    _LOGGER.debug("Probe start: host=%s trying ports=%s", host, WEBSOCKET_PORTS)

    def _get_info_with_port(port: int) -> dict[str, Any] | None:
        try:
            from samsungtvws import SamsungTVWS  # type: ignore

            tv = SamsungTVWS(host, port=port, name=CLIENT_NAME)
            try:
                return tv.rest_device_info()
            finally:
                close_fn = getattr(tv, "close", None)
                if callable(close_fn):
                    close_fn()
        except Exception:
            return None

    # Prefer SSL port first
    for port in WEBSOCKET_PORTS:
        _LOGGER.debug("Probing device info on %s:%s", host, port)
        info = await asyncio.to_thread(_get_info_with_port, port)
        if info:
            dev = info.get("device", {}) if isinstance(info, dict) else {}
            _LOGGER.info(
                "Probe success: host=%s port=%s model=%s name=%s duid=%s udn=%s",
                host,
                port,
                dev.get("modelName"),
                dev.get("name"),
                dev.get("duid"),
                dev.get("udn"),
            )
            return port, info
        _LOGGER.debug("Probe failed on %s:%s (no info)", host, port)
    return None, None


async def async_try_connect(host: str, port: int, token: Optional[str], token_file_path: Optional[str] = None) -> PairResult:
    """Try a connection and return result semantics similar to official bridge.

    RESULT_SUCCESS when token is valid or user accepted pairing and token became available.
    RESULT_AUTH_MISSING when user has not yet accepted.
    RESULT_CANNOT_CONNECT if unreachable.
    """

    _LOGGER.debug(
        "Connect attempt: host=%s port=%s token_present=%s",
        host,
        port,
        bool(token),
    )

    # Try native async websocket first (closer to official integration behavior)
    try:
        from samsungtvws.async_remote import SamsungTVWSAsyncRemote  # type: ignore
        from samsungtvws.exceptions import UnauthorizedError  # type: ignore

        _LOGGER.info("AsyncRemote connect: host=%s port=%s token_present=%s", host, port, bool(token))

        # Retry window to allow the user to accept the prompt without requiring another submit
        max_attempts = 10
        for attempt in range(1, max_attempts + 1):
            _LOGGER.debug("AsyncRemote attempt %s/%s for %s:%s", attempt, max_attempts, host, port)
            async with SamsungTVWSAsyncRemote(
                host=host,
                port=port,
                token=token or "",
                name=CLIENT_NAME,
                timeout=31,
            ) as remote:
                try:
                    await remote.open()
                except UnauthorizedError:
                    _LOGGER.info("Auth missing (AsyncRemote): host=%s port=%s (attempt %s)", host, port, attempt)
                    # Small delay to allow acceptance and retry within the same submission
                    if attempt < max_attempts:
                        await asyncio.sleep(3)
                        continue
                    return PairResult(RESULT_AUTH_MISSING)

                new_token = getattr(remote, "token", None)
                # Fallback to token file if provided
                if not new_token and token_file_path:
                    try:
                        import os

                        if os.path.exists(token_file_path):
                            with open(token_file_path, "r", encoding="utf-8") as f:
                                file_token = f.read().strip()
                                if file_token:
                                    new_token = file_token
                    except Exception:  # noqa: BLE001
                        new_token = None

                if new_token:
                    _LOGGER.info("Connect success (AsyncRemote): host=%s token_captured=True", host)
                    return PairResult(RESULT_SUCCESS, token=new_token)
                # No token but no UnauthorizedError either; brief delay and retry
                if attempt < max_attempts:
                    await asyncio.sleep(2)
                    continue
                _LOGGER.info("Auth missing after AsyncRemote open: host=%s", host)
                return PairResult(RESULT_AUTH_MISSING)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("AsyncRemote path failed, falling back to sync: %r", err, exc_info=True)

    def _attempt() -> PairResult:
        try:
            from samsungtvws import SamsungTVWS  # type: ignore

            if token:
                tv = SamsungTVWS(host, port=port, token=token, name=CLIENT_NAME)
            else:
                # Provide token_file to ensure token is persisted when user accepts
                if token_file_path:
                    tv = SamsungTVWS(host, port=port, token_file=token_file_path, name=CLIENT_NAME)
                else:
                    tv = SamsungTVWS(host, port=port, name=CLIENT_NAME)

            # Touch art / device info to provoke handshake
            try:
                # Provoke pairing prompt on Frame models
                try:
                    tv.art().supported()
                except Exception:
                    pass
                try:
                    tv.art().get_artmode()
                except Exception:
                    pass
                try:
                    tv.art().available()
                except Exception:
                    pass
                info = tv.rest_device_info()
            except Exception:
                info = None

            # If we already have a token or one was persisted, consider success
            new_token = getattr(tv, "token", None)
            # If token not set on object, try reading token_file if provided
            if not new_token and token_file_path:
                try:
                    import os

                    if os.path.exists(token_file_path):
                        with open(token_file_path, "r", encoding="utf-8") as f:
                            file_token = f.read().strip()
                            if file_token:
                                new_token = file_token
                except Exception:  # noqa: BLE001
                    new_token = None
            if new_token:
                # Close before returning to flush token file writes in the library
                close_fn = getattr(tv, "close", None)
                if callable(close_fn):
                    close_fn()
                _LOGGER.info(
                    "Connect success: host=%s port=%s token_captured=%s",
                    host,
                    port,
                    True,
                )
                return PairResult(RESULT_SUCCESS, token=new_token, info=info or {})

            # No token yet, likely needs user acceptance
            close_fn = getattr(tv, "close", None)
            if callable(close_fn):
                close_fn()
            _LOGGER.info("Auth missing: host=%s port=%s (accept on TV)", host, port)
            return PairResult(RESULT_AUTH_MISSING)
        except Exception:
            _LOGGER.debug("Cannot connect: host=%s port=%s", host, port)
            return PairResult(RESULT_CANNOT_CONNECT)

    return await asyncio.to_thread(_attempt)


async def async_encrypted_start_pairing(host: str):
    """Start encrypted pairing and return authenticator-like object with try_pin/get_session_id.

    We keep it minimal and defer to samsungtvws encrypted classes.
    """
    from samsungtvws.encrypted.authenticator import SamsungTVEncryptedWSAsyncAuthenticator  # type: ignore
    auth = SamsungTVEncryptedWSAsyncAuthenticator(host)
    await auth.start_pairing()
    return auth


async def async_encrypted_try_pin(auth, pin: str) -> PairResult:
    try:
        token = await auth.try_pin(pin)
        if not token:
            return PairResult(RESULT_INVALID_PIN)
        session_id = await auth.get_session_id_and_close()
        return PairResult(RESULT_SUCCESS, token=token, session_id=session_id)
    except Exception:  # noqa: BLE001
        return PairResult(RESULT_INVALID_PIN)


