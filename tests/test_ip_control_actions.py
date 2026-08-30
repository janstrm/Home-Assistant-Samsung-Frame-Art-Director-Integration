"""Tests for explicit Home Assistant IP Control power actions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director import async_setup
from custom_components.samsung_frame_art_director.const import DOMAIN
from custom_components.samsung_frame_art_director.ip_control import (
    IPControlAuthError,
    IPControlProtocolError,
    IPControlTransportError,
    IPControlUnavailableError,
)
from custom_components.samsung_frame_art_director.ip_control_actions import (
    async_execute_ip_control_action,
)
from custom_components.samsung_frame_art_director.runtime import (
    SamsungFrameRuntimeData,
)

_ACTIONS = "custom_components.samsung_frame_art_director.ip_control_actions"


def test_power_on_transport_error_falls_back_to_configured_wake_on_lan():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock())
    entry = SimpleNamespace(
        data={
            "host": "192.168.68.61",
            "ip_control_token": "IP-TOKEN",
            "ip_control_port": 1516,
        },
        options={
            "mac_address": "AA:BB:CC:DD:EE:FF",
            "use_wol_before_on": True,
        },
    )
    target = SimpleNamespace(entry=entry)
    ip_client = MagicMock()
    ip_client.async_power_on = AsyncMock(
        side_effect=IPControlTransportError("TV is asleep")
    )

    with (
        patch(f"{_ACTIONS}.SamsungIPControlClient", return_value=ip_client),
        patch(
            "custom_components.samsung_frame_art_director._send_magic_packet"
        ) as send_magic_packet,
    ):
        asyncio.run(
            async_execute_ip_control_action(hass, target, "power_on")
        )

    hass.async_add_executor_job.assert_awaited_once_with(
        send_magic_packet,
        "AA:BB:CC:DD:EE:FF",
        ["192.168.68.255"],
    )


def _add_frame(hass, *, host: str, token: str | None):
    data = {"host": host, "token": f"WS-{host}"}
    if token is not None:
        data.update({"ip_control_token": token, "ip_control_port": 1516})
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=host)
    entry.add_to_hass(hass)
    entry.runtime_data = SamsungFrameRuntimeData(client=MagicMock())
    entity = er.async_get(hass).async_get_or_create(
        "media_player",
        DOMAIN,
        host,
        config_entry=entry,
        suggested_object_id=host.replace(".", "_"),
    )
    return entry, entity.entity_id


@pytest.fixture
async def ip_control_actions(hass):
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    return _add_frame(hass, host="frame.local", token="IP-TOKEN")


@pytest.mark.parametrize(
    ("service", "method"),
    [
        ("power_on", "async_power_on"),
        ("power_off", "async_power_off"),
        ("reboot", "async_reboot"),
    ],
)
async def test_explicit_power_action_routes_to_selected_ip_control_method(
    hass, ip_control_actions, service, method
):
    _, entity_id = ip_control_actions
    ip_client = MagicMock()
    ip_client.async_power_on = AsyncMock()
    ip_client.async_power_off = AsyncMock()
    ip_client.async_reboot = AsyncMock()

    with patch(
        f"{_ACTIONS}.SamsungIPControlClient", return_value=ip_client
    ) as client_type:
        await hass.services.async_call(
            DOMAIN,
            service,
            {"entity_id": entity_id},
            blocking=True,
        )

    client_type.assert_called_once_with(
        hass,
        "frame.local",
        token="IP-TOKEN",
        port=1516,
    )
    getattr(ip_client, method).assert_awaited_once_with()
    for other_method in {"async_power_on", "async_power_off", "async_reboot"} - {
        method
    }:
        getattr(ip_client, other_method).assert_not_awaited()


async def test_power_action_rejects_a_frame_without_ip_control_pairing(
    hass, ip_control_actions
):
    entry, entity_id = ip_control_actions
    hass.config_entries.async_update_entry(
        entry,
        data={"host": "frame.local", "token": "WS-frame.local"},
    )

    with pytest.raises(ServiceValidationError, match="Reconfigure.*IP Control"):
        await hass.services.async_call(
            DOMAIN,
            "power_off",
            {"entity_id": entity_id},
            blocking=True,
        )


async def test_rejected_ip_token_starts_linked_repair_without_exposing_token(
    hass, ip_control_actions, caplog
):
    entry, entity_id = ip_control_actions
    ip_client = MagicMock()
    ip_client.async_power_off = AsyncMock(
        side_effect=IPControlAuthError("SECRET rejected token")
    )

    with (
        patch(f"{_ACTIONS}.SamsungIPControlClient", return_value=ip_client),
        patch.object(entry, "async_start_reauth") as start_reauth,
        pytest.raises(
            ServiceValidationError, match="authorization.*pair again"
        ) as raised,
    ):
        await hass.services.async_call(
            DOMAIN,
            "power_off",
            {"entity_id": entity_id},
            blocking=True,
        )

    start_reauth.assert_called_once_with(
        hass,
        data={"reauth_connection": "ip_control"},
    )
    assert "SECRET" not in caplog.text
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (IPControlUnavailableError("SECRET state"), "unavailable"),
        (IPControlTransportError("SECRET transport"), "Cannot reach"),
        (IPControlProtocolError("SECRET response"), "unexpected"),
    ],
)
async def test_power_action_maps_tv_errors_without_exposing_details(
    hass, ip_control_actions, error, message
):
    _, entity_id = ip_control_actions
    ip_client = MagicMock()
    ip_client.async_reboot = AsyncMock(side_effect=error)

    with (
        patch(f"{_ACTIONS}.SamsungIPControlClient", return_value=ip_client),
        pytest.raises(ServiceValidationError, match=message) as raised,
    ):
        await hass.services.async_call(
            DOMAIN,
            "reboot",
            {"entity_id": entity_id},
            blocking=True,
        )

    assert "SECRET" not in str(raised.value)
    assert raised.value.__cause__ is None


async def test_multi_frame_actions_keep_ip_tokens_isolated(hass):
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    _, first_entity_id = _add_frame(
        hass, host="frame-a.local", token="IP-TOKEN-A"
    )
    _, second_entity_id = _add_frame(
        hass, host="frame-b.local", token="IP-TOKEN-B"
    )
    first_client = MagicMock()
    first_client.async_power_on = AsyncMock()
    second_client = MagicMock()
    second_client.async_power_on = AsyncMock()

    with patch(
        f"{_ACTIONS}.SamsungIPControlClient",
        side_effect=[first_client, second_client],
    ) as client_type:
        await hass.services.async_call(
            DOMAIN,
            "power_on",
            {"entity_id": first_entity_id},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            "power_on",
            {"entity_id": second_entity_id},
            blocking=True,
        )

    assert client_type.call_args_list == [
        call(
            hass,
            "frame-a.local",
            token="IP-TOKEN-A",
            port=1516,
        ),
        call(
            hass,
            "frame-b.local",
            token="IP-TOKEN-B",
            port=1516,
        ),
    ]
    first_client.async_power_on.assert_awaited_once_with()
    second_client.async_power_on.assert_awaited_once_with()
