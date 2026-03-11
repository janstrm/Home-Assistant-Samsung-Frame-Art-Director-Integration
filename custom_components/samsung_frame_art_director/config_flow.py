"""Config flow for Samsung Frame Art Director (bridge-based pairing)."""

from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from .bridge import async_probe_device_info, async_try_connect, async_encrypted_start_pairing, async_encrypted_try_pin
from .const import (
    DOMAIN,
    CONF_DUID,
    RESULT_AUTH_MISSING,
    RESULT_CANNOT_CONNECT,
    RESULT_NOT_SUPPORTED,
    RESULT_SUCCESS,
    ENCRYPTED_WEBSOCKET_PORT,
    CONF_SLIDESHOW_INTERVAL,
    CONF_SLIDESHOW_SOURCE_PATH,
    CONF_SLIDESHOW_ENABLED,
    CONF_SLIDESHOW_SOURCE_TYPE,
    CONF_SLIDESHOW_FILTER,
    CONF_GEMINI_API_KEY,
    SLIDESHOW_SOURCE_FOLDER,
    SLIDESHOW_SOURCE_TAGS,
    SLIDESHOW_SOURCE_LIBRARY,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str, vol.Optional(CONF_NAME, default="Samsung Frame"): str})


class SamsungFrameConfigFlow(config_entries.ConfigFlow, domain="samsung_frame_art_director"):
    """Handle a config flow for Samsung Frame Art Director."""

    VERSION = 2

    def __init__(self) -> None:
        self._host: str | None = None
        self._name: str | None = None
        self._port: int | None = None
        self._device_info: dict[str, Any] | None = None
        self._duid: str | None = None
        self._token: str | None = None
        self._encrypted_auth = None

    async def async_step_user(self, user_input: dict | None = None):
        # Enable verbose logs during flow to capture early diagnostics
        try:
            logging.getLogger("custom_components.samsung_frame_art_director").setLevel(logging.DEBUG)
            logging.getLogger("samsung_frame_art_director").setLevel(logging.DEBUG)
            logging.getLogger(__name__).setLevel(logging.DEBUG)
            logging.getLogger("samsungtvws").setLevel(logging.INFO)
        except Exception:  # noqa: BLE001
            pass
        _LOGGER.debug("Flow step_user: user_input=%s", bool(user_input))
        errors: dict[str, str] | None = None
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._name = user_input.get(CONF_NAME, "Samsung Frame")
            _LOGGER.debug("User provided host=%s name=%s", self._host, self._name)
            port, info = await async_probe_device_info(self._host)
            if not port:
                _LOGGER.debug("Probe failed for host=%s", self._host)
                errors = {"base": RESULT_CANNOT_CONNECT}
            else:
                self._port = port
                self._device_info = info or {}
                dev = self._device_info.get("device", {}) if isinstance(self._device_info, dict) else {}
                self._duid = dev.get("duid") or dev.get("udn") or self._host
                _LOGGER.info(
                    "Probe selected: host=%s port=%s duid/udn=%s",
                    self._host,
                    self._port,
                    self._duid,
                )
                # Set unique id early; update host on duplicates
                await self.async_set_unique_id(self._duid, raise_on_progress=False)
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})
                # If device info indicates legacy/encrypted models, offer encrypted pairing path
                model = (self._device_info or {}).get("device", {}).get("modelName")
                if model and isinstance(model, str) and model.upper().startswith(("H", "J")):
                    self._port = ENCRYPTED_WEBSOCKET_PORT
                    self.context["title_placeholders"] = {"device": self._name or self._host}
                    return await self.async_step_encrypted_pairing()
                self.context["title_placeholders"] = {"device": self._name or self._host}
                return await self.async_step_pairing()

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors or {},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_pairing(self, user_input: dict | None = None):
        _LOGGER.debug(
            "Flow step_pairing: host=%s port=%s token_present=%s submitted=%s",
            self._host,
            self._port,
            bool(self._token),
            bool(user_input),
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._host and self._port
            # Use a persistent token_file path to guarantee we can read the token after acceptance
            safe_host = str(self._host).replace("/", "_").replace(".", "_")
            token_file_path = self.hass.config.path(f"pairing_tokens/token_{safe_host}.txt")
            try:
                os.makedirs(os.path.dirname(token_file_path), exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
            _LOGGER.debug("Pairing token_file_path=%s", token_file_path)
            result = await async_try_connect(self._host, self._port, self._token, token_file_path=token_file_path)
            if result.result == RESULT_SUCCESS:
                self._token = result.token or self._token
                _LOGGER.info("Pairing success: host=%s token_present=%s", self._host, bool(self._token))
                data = {
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_NAME: self._name,
                    CONF_DUID: self._duid,
                }
                # Persist method for parity; encrypted step sets port to ENCRYPTED_WEBSOCKET_PORT
                if self._port == ENCRYPTED_WEBSOCKET_PORT:
                    data["method"] = "encrypted"
                else:
                    data["method"] = "websocket"
                if self._token:
                    data["token"] = self._token
                return self.async_create_entry(title=self._name or self._host, data=data)
            if result.result == RESULT_AUTH_MISSING:
                _LOGGER.debug("Pairing pending (auth_missing): host=%s", self._host)
                errors = {"base": RESULT_AUTH_MISSING}
            elif result.result == RESULT_CANNOT_CONNECT:
                _LOGGER.debug("Pairing failed (cannot_connect): host=%s", self._host)
                errors = {"base": RESULT_CANNOT_CONNECT}
            else:
                _LOGGER.debug("Pairing failed (not_supported): host=%s", self._host)
                errors = {"base": RESULT_NOT_SUPPORTED}

        self.context["title_placeholders"] = {"device": self._name or self._host}
        return self.async_show_form(
            step_id="pairing",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"device": self._name or self._host},
        )

    async def async_step_encrypted_pairing(self, user_input: dict | None = None):
        _LOGGER.debug("Flow step_encrypted_pairing: host=%s submitted=%s", self._host, bool(user_input))
        errors: dict[str, str] = {}
        if self._encrypted_auth is None and self._host:
            try:
                self._encrypted_auth = await async_encrypted_start_pairing(self._host)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Encrypted pairing start failed: %r", err, exc_info=True)
                errors = {"base": RESULT_CANNOT_CONNECT}

        if user_input is not None and self._encrypted_auth is not None:
            pin = user_input.get("pin")
            res = await async_encrypted_try_pin(self._encrypted_auth, pin)
            if res.result == RESULT_SUCCESS:
                data = {
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_NAME: self._name,
                    CONF_DUID: self._duid,
                    "token": res.token,
                    "session_id": res.session_id,
                }
                return self.async_create_entry(title=self._name or self._host, data=data)
            errors = {"base": RESULT_INVALID_PIN}

        self.context["title_placeholders"] = {"device": self._name or self._host}
        return self.async_show_form(
            step_id="encrypted_pairing",
            data_schema=vol.Schema({vol.Required("pin"): str}),
            errors=errors,
            description_placeholders={"device": self._name or self._host},
        )


# Options Flow
class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:  # type: ignore[name-defined]
        self._entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(title="Options", data=user_input)
        opts = self._entry.options or {}
        schema = vol.Schema(
            {
                vol.Optional("mac_address", default=opts.get("mac_address", "")): str,
                vol.Optional("use_wol_before_on", default=opts.get("use_wol_before_on", False)): bool,
                vol.Optional("use_power_key_on_off", default=opts.get("use_power_key_on_off", False)): bool,
                vol.Optional(CONF_SLIDESHOW_ENABLED, default=opts.get(CONF_SLIDESHOW_ENABLED, False)): bool,
                vol.Optional(CONF_SLIDESHOW_INTERVAL, default=opts.get(CONF_SLIDESHOW_INTERVAL, 0)): int,
                vol.Optional(CONF_SLIDESHOW_SOURCE_TYPE, default=opts.get(CONF_SLIDESHOW_SOURCE_TYPE, SLIDESHOW_SOURCE_FOLDER)): vol.In([SLIDESHOW_SOURCE_FOLDER, SLIDESHOW_SOURCE_TAGS, SLIDESHOW_SOURCE_LIBRARY]),
                vol.Optional(CONF_SLIDESHOW_FILTER, default=opts.get(CONF_SLIDESHOW_FILTER, "")): str,
                vol.Optional(CONF_GEMINI_API_KEY, default=opts.get(CONF_GEMINI_API_KEY, "")): str,
                vol.Optional(CONF_SLIDESHOW_SOURCE_PATH, default=opts.get(CONF_SLIDESHOW_SOURCE_PATH, "/media/frame/library")): str,
                vol.Optional("cleanup_max_items", default=opts.get("cleanup_max_items", 50)): int,
                vol.Optional("cleanup_max_age_days", default=opts.get("cleanup_max_age_days", 0)): int,
                vol.Optional("cleanup_preserve_current", default=opts.get("cleanup_preserve_current", True)): bool,
                vol.Optional("cleanup_only_integration_managed", default=opts.get("cleanup_only_integration_managed", True)): bool,
                vol.Optional("cleanup_dry_run", default=opts.get("cleanup_dry_run", False)): bool,
                vol.Optional("diagnostics_verbose", default=opts.get("diagnostics_verbose", True)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


        return self.async_show_form(step_id="init", data_schema=schema)
