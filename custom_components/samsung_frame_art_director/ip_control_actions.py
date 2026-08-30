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


async def _async_try_wake_on_lan(
    hass: HomeAssistant,
    target: FrameActionTarget,
) -> bool:
    """Wake a sleeping TV when its configured IP Control port is offline."""
    options = target.entry.options
    mac = options.get("mac_address")
    if not options.get("use_wol_before_on") or not isinstance(mac, str) or not mac:
        return False

    host = target.entry.data[CONF_HOST]
    broadcasts = (
        [host.rsplit(".", 1)[0] + ".255"]
        if isinstance(host, str) and host.count(".") == 3
        else []
    )
    # Imported lazily because the package registers this action module while
    # defining the shared Wake-on-LAN helper.
    from . import _send_magic_packet

    try:
        await hass.async_add_executor_job(_send_magic_packet, mac, broadcasts)
    except (OSError, ValueError):
        return False
    return True


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
        if action == "power_on" and await _async_try_wake_on_lan(hass, target):
            return
        raise ServiceValidationError(
            "Cannot reach IP Control on the selected TV. Verify that the TV "
            "is reachable on the local network, or configure Wake-on-LAN "
            "with its MAC address."
        ) from None
    except IPControlProtocolError:
        raise ServiceValidationError(
            "The selected TV returned an unexpected IP Control response."
        ) from None
