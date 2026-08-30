"""Tests for the config flow (user pairing, reconfigure guard)."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.bridge import PairResult
from custom_components.samsung_frame_art_director.const import DOMAIN, RESULT_SUCCESS
from custom_components.samsung_frame_art_director.ip_control import (
    IPControlAuthError,
    IPControlProtocolError,
    IPControlTransportError,
    IPControlUnavailableError,
)

_CF = "custom_components.samsung_frame_art_director.config_flow"


@pytest.mark.parametrize(
    "path",
    [
        Path("custom_components/samsung_frame_art_director/strings.json"),
        Path(
            "custom_components/samsung_frame_art_director/translations/en.json"
        ),
    ],
)
def test_flow_title_does_not_require_runtime_placeholders(path):
    strings = json.loads(path.read_text(encoding="utf-8"))
    assert strings["config"]["flow_title"] == "Samsung Frame Art Director"
    assert "{" not in strings["config"]["step"]["reconfigure"]["title"]


async def test_user_flow_success(hass):
    with patch(
        f"{_CF}.async_probe_device_info",
        AsyncMock(return_value=(8002, {"device": {"duid": "DUID1", "name": "Frame", "modelName": "QN65LS03D"}})),
    ), patch(
        f"{_CF}.async_try_connect",
        AsyncMock(return_value=PairResult(RESULT_SUCCESS, token="TOK")),
    ), patch(
        "custom_components.samsung_frame_art_director.async_setup_entry",
        return_value=True,
    ), patch(
        f"{_CF}.async_pair_ip_control",
        AsyncMock(),
    ) as ip_control_pair:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "1.2.3.4", "name": "Frame"}
        )
        # Pairing step shows a form (user accepts on the TV), then submits.
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["host"] == "1.2.3.4"
        assert result["data"]["token"] == "TOK"
        ip_control_pair.assert_not_awaited()


async def test_user_flow_cannot_connect(hass):
    with patch(f"{_CF}.async_probe_device_info", AsyncMock(return_value=(None, None))):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "1.2.3.4", "name": "Frame"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"


async def test_dhcp_enriches_mac_for_existing_entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="DUID1", data={"host": "1.2.3.4"}, options={})
    entry.add_to_hass(hass)

    info = SimpleNamespace(ip="1.2.3.4", hostname="samsung", macaddress="aabbccddeeff")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=info
    )
    assert result["type"] == FlowResultType.ABORT
    assert entry.options.get("mac_address") == "aa:bb:cc:dd:ee:ff"


async def test_reconfigure_rejects_different_device(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="DUID1", data={"host": "1.2.3.4"})
    entry.add_to_hass(hass)
    with patch(
        f"{_CF}.async_probe_device_info",
        AsyncMock(return_value=(8002, {"device": {"duid": "OTHER"}})),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == FlowResultType.MENU
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_connection"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "9.9.9.9", "name": "Frame"}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "wrong_device"


async def test_ip_control_pairing_preserves_entry_data_and_replaces_stale_token(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DUID1",
        data={
            "host": "1.2.3.4",
            "name": "Frame",
            "port": 8002,
            "token": "WEBSOCKET_TOKEN",
            "ip_control_token": "STALE_IP_TOKEN",
            "custom": "keep-me",
        },
    )
    entry.add_to_hass(hass)
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DUID2",
        data={
            "host": "5.6.7.8",
            "ip_control_token": "OTHER_IP_TOKEN",
            "ip_control_port": 1515,
        },
    )
    other_entry.add_to_hass(hass)

    with patch(
        f"{_CF}.async_pair_ip_control",
        AsyncMock(return_value=("FRESH_IP_TOKEN", 1516)),
        create=True,
    ) as pair:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == FlowResultType.MENU
        assert result["menu_options"] == ["reconfigure_connection", "ip_control"]
        assert result["description_placeholders"] == {"device": "Frame"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ip_control"}
        )
        assert result["type"] == FlowResultType.FORM
        assert pair.await_count == 0

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "ip_control_pairing_successful"
    pair.assert_awaited_once_with(hass, "1.2.3.4")
    assert entry.data == {
        "host": "1.2.3.4",
        "name": "Frame",
        "port": 8002,
        "token": "WEBSOCKET_TOKEN",
        "ip_control_token": "FRESH_IP_TOKEN",
        "ip_control_port": 1516,
        "custom": "keep-me",
    }
    assert other_entry.data == {
        "host": "5.6.7.8",
        "ip_control_token": "OTHER_IP_TOKEN",
        "ip_control_port": 1515,
    }


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (IPControlTransportError("SECRET transport detail"), "cannot_connect"),
        (IPControlAuthError("SECRET rejected token"), "ip_control_rejected"),
        (IPControlUnavailableError("SECRET TV state"), "ip_control_unavailable"),
        (IPControlProtocolError("SECRET response"), "ip_control_pairing_failed"),
    ],
)
async def test_ip_control_pairing_errors_are_classified_and_redacted(
    hass, caplog, error, expected_error
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DUID1",
        data={"host": "1.2.3.4", "token": "WEBSOCKET_TOKEN"},
    )
    entry.add_to_hass(hass)

    with patch(
        f"{_CF}.async_pair_ip_control", AsyncMock(side_effect=error)
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ip_control"}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == expected_error
    assert "SECRET" not in repr(result)
    assert "SECRET" not in caplog.text
    assert "ip_control_token" not in entry.data
    assert entry.data["token"] == "WEBSOCKET_TOKEN"


async def test_ip_control_connection_refusal_falls_back_then_shows_cannot_connect(
    hass,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DUID1",
        data={"host": "1.2.3.4", "token": "WEBSOCKET_TOKEN"},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.samsung_frame_art_director.ip_control.http.client.HTTPSConnection",
        side_effect=ConnectionRefusedError,
    ) as connection:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ip_control"}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"
    assert [item.args[1] for item in connection.call_args_list] == [1516, 1515]
    assert "ip_control_token" not in entry.data


async def test_stale_ip_control_token_opens_linked_repair_flow_without_auto_pairing(
    hass,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DUID1",
        data={
            "host": "1.2.3.4",
            "token": "WEBSOCKET_TOKEN",
            "ip_control_token": "STALE_IP_TOKEN",
            "ip_control_port": 1516,
        },
    )
    entry.add_to_hass(hass)

    with patch(
        f"{_CF}.async_pair_ip_control",
        AsyncMock(return_value=("FRESH_IP_TOKEN", 1516)),
    ) as pair:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data={**entry.data, "reauth_connection": "ip_control"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "ip_control"
        pair.assert_not_awaited()

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.ABORT
    assert entry.data["ip_control_token"] == "FRESH_IP_TOKEN"
    assert entry.data["token"] == "WEBSOCKET_TOKEN"
