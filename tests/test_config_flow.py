"""Tests for the config flow (user pairing, reconfigure guard)."""
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.bridge import PairResult
from custom_components.samsung_frame_art_director.const import DOMAIN, RESULT_SUCCESS

_CF = "custom_components.samsung_frame_art_director.config_flow"


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
    ):
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
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

    entry = MockConfigEntry(domain=DOMAIN, unique_id="DUID1", data={"host": "1.2.3.4"}, options={})
    entry.add_to_hass(hass)

    info = DhcpServiceInfo(ip="1.2.3.4", hostname="samsung", macaddress="aabbccddeeff")
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
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "9.9.9.9", "name": "Frame"}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "wrong_device"
