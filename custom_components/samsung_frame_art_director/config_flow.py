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
    RESULT_INVALID_PIN,
    ENCRYPTED_WEBSOCKET_PORT,
    CONF_SLIDESHOW_INTERVAL,
    CONF_SLIDESHOW_SOURCE_PATH,
    CONF_SLIDESHOW_ENABLED,
    CONF_SLIDESHOW_SOURCE_TYPE,
    CONF_SLIDESHOW_FILTER,
    CONF_GEMINI_API_KEY,
    CONF_OPENAI_API_KEY,
    CONF_AI_PROVIDER,
    CONF_AI_MODEL,
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OPENAI,
    SLIDESHOW_SOURCE_FOLDER,
    SLIDESHOW_SOURCE_TAGS,
    SLIDESHOW_SOURCE_LIBRARY,
    CONF_INBOX_DIR,
    CONF_LIBRARY_DIR,
    DEFAULT_INBOX_DIR,
    DEFAULT_LIBRARY_DIR,
    CONF_RESIZE_MODE,
    RESIZE_MODE_CROP,
    RESIZE_MODE_FIT,
    DEFAULT_RESIZE_MODE,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str, vol.Optional(CONF_NAME, default="Samsung Frame"): str})


def _normalize_host(raw: str) -> str:
    """Clean up user-entered host: trim, drop scheme/path, and a trailing port.

    Accepts things like " http://192.168.1.5:8002/ " and returns "192.168.1.5".
    Leaves bracketed IPv6 literals untouched.
    """
    host = (raw or "").strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    # Drop any path component
    host = host.split("/", 1)[0]
    # Strip a trailing :port for hostnames/IPv4 (but not IPv6, which has many colons)
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.strip()


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
            self._host = _normalize_host(user_input[CONF_HOST])
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

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Handle re-authentication when the stored token is no longer valid."""
        self._host = entry_data.get(CONF_HOST)
        self._port = entry_data.get(CONF_PORT)
        self._name = entry_data.get(CONF_NAME)
        self._duid = entry_data.get(CONF_DUID)
        _LOGGER.debug("Flow step_reauth: host=%s port=%s", self._host, self._port)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None):
        """Re-pair with the TV and update the stored token."""
        errors: dict[str, str] = {}
        if user_input is not None and self._host:
            safe_host = str(self._host).replace("/", "_").replace(".", "_")
            token_file_path = self.hass.config.path(f"pairing_tokens/token_{safe_host}.txt")
            try:
                os.makedirs(os.path.dirname(token_file_path), exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
            # Re-pair without the old token to force a fresh acceptance + token.
            result = await async_try_connect(
                self._host, self._port or 8002, None, token_file_path=token_file_path
            )
            if result.result == RESULT_SUCCESS:
                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                _LOGGER.info("Reauth success: host=%s", self._host)
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, "token": result.token or entry.data.get("token")}
                )
            if result.result == RESULT_AUTH_MISSING:
                errors = {"base": RESULT_AUTH_MISSING}
            elif result.result == RESULT_CANNOT_CONNECT:
                errors = {"base": RESULT_CANNOT_CONNECT}
            else:
                errors = {"base": RESULT_NOT_SUPPORTED}

        self.context["title_placeholders"] = {"device": self._name or self._host}
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"device": self._name or self._host},
        )

    async def async_step_zeroconf(self, discovery_info):
        """Handle a Samsung TV discovered via zeroconf (_samsungmsf._tcp)."""
        host = _normalize_host(getattr(discovery_info, "host", "") or "")
        if not host:
            return self.async_abort(reason="cannot_connect")
        props = getattr(discovery_info, "properties", {}) or {}
        _LOGGER.debug("Zeroconf discovery: host=%s props=%s", host, props)

        port, info = await async_probe_device_info(host)
        if not port:
            return self.async_abort(reason="cannot_connect")
        self._host = host
        self._port = port
        self._device_info = info or {}
        dev = self._device_info.get("device", {}) if isinstance(self._device_info, dict) else {}
        self._duid = dev.get("duid") or dev.get("udn") or host
        self._name = dev.get("name") or props.get("fn") or "Samsung Frame"

        await self.async_set_unique_id(self._duid)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})

        self.context["title_placeholders"] = {"device": self._name or self._host}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input: dict | None = None):
        """Confirm setup of a discovered TV, then continue to pairing."""
        if user_input is not None:
            model = (self._device_info or {}).get("device", {}).get("modelName")
            if model and isinstance(model, str) and model.upper().startswith(("H", "J")):
                self._port = ENCRYPTED_WEBSOCKET_PORT
                return await self.async_step_encrypted_pairing()
            return await self.async_step_pairing()

        self.context["title_placeholders"] = {"device": self._name or self._host}
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"device": self._name or self._host},
        )

    async def async_step_reconfigure(self, user_input: dict | None = None):
        """Change the TV's IP/name without removing the integration."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}
        if user_input is not None and entry:
            new_host = _normalize_host(user_input[CONF_HOST])
            port, info = await async_probe_device_info(new_host)
            if not port:
                errors = {"base": RESULT_CANNOT_CONNECT}
            else:
                dev = (info or {}).get("device", {}) if isinstance(info, dict) else {}
                duid = dev.get("duid") or dev.get("udn") or new_host
                # Guard against pointing the entry at a different TV.
                if entry.unique_id and duid != entry.unique_id:
                    return self.async_abort(reason="wrong_device")
                new_data = {**entry.data, CONF_HOST: new_host, CONF_PORT: port}
                if user_input.get(CONF_NAME):
                    new_data[CONF_NAME] = user_input[CONF_NAME]
                return self.async_update_reload_and_abort(entry, data=new_data)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(entry.data.get(CONF_HOST) if entry else "")): str,
                vol.Optional(CONF_NAME, default=(entry.data.get(CONF_NAME, "Samsung Frame") if entry else "Samsung Frame")): str,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)


# Options Flow
class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:  # type: ignore[name-defined]
        self._entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            # Merge with existing options so settings managed by entities
            # (matte style/color, favorites-only, etc.) that aren't part of
            # this form are preserved rather than wiped on save.
            new_options = {**(self._entry.options or {}), **user_input}
            return self.async_create_entry(title="", data=new_options)
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
                vol.Optional(CONF_AI_PROVIDER, default=opts.get(CONF_AI_PROVIDER, AI_PROVIDER_GEMINI)): vol.In([AI_PROVIDER_GEMINI, AI_PROVIDER_OPENAI]),
                vol.Optional(CONF_AI_MODEL, default=opts.get(CONF_AI_MODEL, "")): str,
                vol.Optional(CONF_GEMINI_API_KEY, default=opts.get(CONF_GEMINI_API_KEY, "")): str,
                vol.Optional(CONF_OPENAI_API_KEY, default=opts.get(CONF_OPENAI_API_KEY, "")): str,
                vol.Optional(CONF_SLIDESHOW_SOURCE_PATH, default=opts.get(CONF_SLIDESHOW_SOURCE_PATH, "/media/frame/library")): str,
                vol.Optional(CONF_INBOX_DIR, default=opts.get(CONF_INBOX_DIR, DEFAULT_INBOX_DIR)): str,
                vol.Optional(CONF_LIBRARY_DIR, default=opts.get(CONF_LIBRARY_DIR, DEFAULT_LIBRARY_DIR)): str,
                vol.Optional(CONF_RESIZE_MODE, default=opts.get(CONF_RESIZE_MODE, DEFAULT_RESIZE_MODE)): vol.In([RESIZE_MODE_CROP, RESIZE_MODE_FIT]),
                vol.Optional("cleanup_max_items", default=opts.get("cleanup_max_items", 50)): int,
                vol.Optional("cleanup_max_age_days", default=opts.get("cleanup_max_age_days", 0)): int,
                vol.Optional("cleanup_preserve_current", default=opts.get("cleanup_preserve_current", True)): bool,
                vol.Optional("cleanup_only_integration_managed", default=opts.get("cleanup_only_integration_managed", True)): bool,
                vol.Optional("cleanup_dry_run", default=opts.get("cleanup_dry_run", False)): bool,
                vol.Optional("diagnostics_verbose", default=opts.get("diagnostics_verbose", True)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
