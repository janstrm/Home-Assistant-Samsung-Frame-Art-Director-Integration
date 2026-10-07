"""User-initiated pairing orchestration for Samsung IP Control."""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING

from .ip_control import (
    DEFAULT_IP_CONTROL_PORT,
    IPControlTransportError,
    SamsungIPControlClient,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LEGACY_IP_CONTROL_PORT = 1515
IP_CONTROL_PAIRING_PORTS = (DEFAULT_IP_CONTROL_PORT, LEGACY_IP_CONTROL_PORT)
_LOGGER = logging.getLogger(__name__)


def _transport_failure_reason(error: BaseException) -> str:
    """Classify the cause without logging exception text or TV responses."""
    seen: set[int] = set()
    while id(error) not in seen and len(seen) < 10:
        seen.add(id(error))
        if isinstance(error, TimeoutError):
            return "timeout"
        if isinstance(error, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(error, ssl.SSLError):
            return "tls_error"
        if error.__cause__ is None:
            break
        error = error.__cause__
    return "transport_error"


async def async_pair_ip_control(
    hass: HomeAssistant, host: str
) -> tuple[str, int]:
    """Pair one TV, falling back only when an endpoint cannot be reached."""
    for index, port in enumerate(IP_CONTROL_PAIRING_PORTS):
        _LOGGER.debug("IP Control: pairing host=%s port=%s", host, port)
        client = SamsungIPControlClient(hass, host, port=port)
        try:
            token = await client.async_pair()
        except IPControlTransportError as err:
            _LOGGER.warning(
                "IP Control: pairing failed host=%s port=%s reason=%s; "
                "enable IP Remote and check this port is reachable from Home Assistant",
                host, port, _transport_failure_reason(err),
            )
            if index + 1 < len(IP_CONTROL_PAIRING_PORTS):
                continue
            raise
        _LOGGER.info("IP Control: pairing succeeded host=%s port=%s", host, port)
        return token, port

    raise IPControlTransportError("No IP Control endpoint was reachable")
