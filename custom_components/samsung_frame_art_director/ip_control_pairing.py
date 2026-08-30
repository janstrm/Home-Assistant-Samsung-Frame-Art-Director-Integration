"""User-initiated pairing orchestration for Samsung IP Control."""

from __future__ import annotations

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


async def async_pair_ip_control(
    hass: HomeAssistant, host: str
) -> tuple[str, int]:
    """Pair one TV, falling back only when an endpoint cannot be reached."""
    for index, port in enumerate(IP_CONTROL_PAIRING_PORTS):
        client = SamsungIPControlClient(hass, host, port=port)
        try:
            token = await client.async_pair()
        except IPControlTransportError:
            if index + 1 < len(IP_CONTROL_PAIRING_PORTS):
                continue
            raise
        return token, port

    raise IPControlTransportError("No IP Control endpoint was reachable")
