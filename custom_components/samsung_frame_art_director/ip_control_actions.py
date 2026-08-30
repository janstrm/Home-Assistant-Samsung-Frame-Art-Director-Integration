"""Explicit Home Assistant action boundary for Samsung IP Control."""

from __future__ import annotations

from typing import Literal

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .const import (
    CONF_IP_CONTROL_PORT,
    CONF_IP_CONTROL_TOKEN,
    DATA_REAUTH_CONNECTION,
    REAUTH_CONNECTION_IP_CONTROL,
)
from .ip_control import (
    IPControlAuthError,
    IPControlProtocolError,
    IPControlTransportError,
    IPControlUnavailableError,
    SamsungIPControlClient,
)
from .targets import FrameActionTarget

IPControlAction = Literal["power_on", "power_off", "reboot"]
IP_CONTROL_ACTIONS: tuple[IPControlAction, ...] = (
    "power_on",
    "power_off",
    "reboot",
)


async def async_execute_ip_control_action(
    hass: HomeAssistant,
    target: FrameActionTarget,
    action: IPControlAction,
) -> None:
    """Execute one explicit action with per-entry credentials."""
    token = target.entry.data.get(CONF_IP_CONTROL_TOKEN)
    port = target.entry.data.get(CONF_IP_CONTROL_PORT)
    if not isinstance(token, str) or not token or not isinstance(port, int):
        raise ServiceValidationError(
            "The selected Frame is not paired for IP Control. "
            "Open Reconfigure > IP Control and pair it first."
        )

    client = SamsungIPControlClient(
        hass,
        target.entry.data[CONF_HOST],
        token=token,
        port=port,
    )
    method = {
        "power_on": client.async_power_on,
        "power_off": client.async_power_off,
        "reboot": client.async_reboot,
    }[action]
    try:
        await method()
    except IPControlAuthError:
        target.entry.async_start_reauth(
            hass,
            data={DATA_REAUTH_CONNECTION: REAUTH_CONNECTION_IP_CONTROL},
        )
        raise ServiceValidationError(
            "IP Control authorization was rejected. Open the Home Assistant "
            "repair and approve the TV prompt to pair again."
        ) from None
    except IPControlUnavailableError:
        raise ServiceValidationError(
            "This IP Control action is unavailable on the selected TV or in "
            "its current state."
        ) from None
    except IPControlTransportError:
        raise ServiceValidationError(
            "Cannot reach IP Control on the selected TV. Verify that the TV "
            "is reachable on the local network."
        ) from None
    except IPControlProtocolError:
        raise ServiceValidationError(
            "The selected TV returned an unexpected IP Control response."
        ) from None
