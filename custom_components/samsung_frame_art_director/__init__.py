"""Samsung Frame Art Director integration."""

import asyncio
import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ServiceValidationError

from .const import (
    CONF_INBOX_DIR,
    CONF_LIBRARY_DIR,
    CONF_MATTE_COLOR,
    CONF_MATTE_ENABLED,
    CONF_MATTE_STYLE,
    CONF_RESIZE_MODE,
    CONF_SLIDESHOW_ENABLED,
    CONF_SLIDESHOW_FILTER,
    CONF_SLIDESHOW_INTERVAL,
    CONF_SLIDESHOW_SOURCE_PATH,
    CONF_SLIDESHOW_SOURCE_TYPE,
    DEFAULT_CLEANUP_DRY_RUN,
    DEFAULT_CLEANUP_MAX_ITEMS,
    DEFAULT_CLEANUP_ONLY_INTEGRATION_MANAGED,
    DEFAULT_CLEANUP_PRESERVE_CURRENT,
    DEFAULT_INBOX_DIR,
    DEFAULT_LIBRARY_DIR,
    DEFAULT_MATTE_COLOR,
    DEFAULT_MATTE_STYLE,
    DEFAULT_RESIZE_MODE,
    DEFAULT_SLIDESHOW_INTERVAL,
    DOMAIN,
    MATTE_STYLE_NONE,
    SLIDESHOW_SOURCE_FOLDER,
    SLIDESHOW_SOURCE_TAGS,
    resolve_matte,
)
from .database import async_prepare_entry_database
from .file_access import (
    UnsafeLocalPathError,
    is_local_media_identifier,
    resolve_upload_source,
)
from .ip_control_actions import (
    IP_CONTROL_ACTIONS,
    async_execute_ip_control_action,
)
from .runtime import SamsungFrameConfigEntry, SamsungFrameRuntimeData
from .targets import (
    async_resolve_action_targets,
    entry_entity_id,
    loaded_frame_target,
    loaded_frame_targets,
)

# This integration is configured via the UI only (config entries), not YAML.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Set up domain-wide interfaces shared by every Frame entry."""
    from .views import SamsungFrameThumbnailView

    hass.http.register_view(SamsungFrameThumbnailView(hass))
    _register_domain_actions(hass)
    _register_domain_websocket(hass)
    return True


PLATFORMS = ["media_player", "number", "switch", "select", "text", "image", "sensor"]

_LOGGER = logging.getLogger(__name__)

MAX_REMOTE_IMAGE_BYTES = 20 * 1024 * 1024
MAX_REMOTE_REDIRECTS = 5
MIN_KEY_HOLD_SECONDS = 0.1
MAX_KEY_HOLD_SECONDS = 30.0


def _cleanup_params(entry: ConfigEntry, overrides=None) -> dict:
    """Build one cleanup policy from config-entry options and call overrides."""
    params = {
        "max_items": entry.options.get("cleanup_max_items", DEFAULT_CLEANUP_MAX_ITEMS),
        "max_age_days": entry.options.get("cleanup_max_age_days") or None,
        "preserve_current": entry.options.get("cleanup_preserve_current", DEFAULT_CLEANUP_PRESERVE_CURRENT),
        "only_integration_managed": entry.options.get(
            "cleanup_only_integration_managed",
            DEFAULT_CLEANUP_ONLY_INTEGRATION_MANAGED,
        ),
        "dry_run": entry.options.get("cleanup_dry_run", DEFAULT_CLEANUP_DRY_RUN),
    }
    for key, value in (overrides or {}).items():
        if key in params:
            params[key] = value
    return params


def _send_magic_packet(mac: str, broadcast_ips: list[str] | None = None) -> None:
    """Send a Wake-on-LAN magic packet to ``mac`` via UDP broadcast.

    Self-contained so it doesn't require the ``wake_on_lan`` integration to be
    set up. Raises on malformed MAC or socket failure so the caller can log it.

    Broadcasts to both the global broadcast address and any provided
    subnet-directed broadcast (e.g. 192.168.68.255), since some switch/AP
    setups only forward the directed broadcast to a sleeping device.
    """
    import socket

    hexmac = mac.replace(":", "").replace("-", "").replace(".", "").strip()
    if len(hexmac) != 12:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    payload = bytes.fromhex("FF" * 6 + hexmac * 16)
    targets = ["255.255.255.255"]
    for ip in broadcast_ips or []:
        if ip and ip not in targets:
            targets.append(ip)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sent = False
        last_error: OSError | None = None
        # Standard WoL ports (9 and 7) on every target broadcast address.
        for ip in targets:
            for port in (9, 7):
                try:
                    sock.sendto(payload, (ip, port))
                    sent = True
                except OSError as err:
                    last_error = err
        if not sent and last_error is not None:
            raise last_error


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the current schema (idempotent)."""
    if entry.version > 3:
        # Downgrade not supported.
        return False
    if entry.version == 3:
        return True

    new_options = dict(entry.options or {})

    # Legacy matte on/off switch -> matte style + color.
    if CONF_MATTE_ENABLED in new_options and CONF_MATTE_STYLE not in new_options:
        if new_options.get(CONF_MATTE_ENABLED):
            new_options[CONF_MATTE_STYLE] = DEFAULT_MATTE_STYLE
            new_options[CONF_MATTE_COLOR] = DEFAULT_MATTE_COLOR
        else:
            new_options[CONF_MATTE_STYLE] = MATTE_STYLE_NONE
    new_options.pop(CONF_MATTE_ENABLED, None)

    # Legacy slideshow_source_dir -> library_dir (only if customised).
    legacy_dir = new_options.pop(CONF_SLIDESHOW_SOURCE_PATH, None)
    if legacy_dir and legacy_dir != DEFAULT_LIBRARY_DIR and not new_options.get(CONF_LIBRARY_DIR):
        new_options[CONF_LIBRARY_DIR] = legacy_dir

    hass.config_entries.async_update_entry(entry, options=new_options, version=3)
    _LOGGER.info("Migrated config entry %s to version 3", entry.entry_id)
    return True


def _enable_verbose_logging() -> None:
    """Enable verbose logging for this integration and samsungtvws at startup."""
    try:
        # Our package under custom_components
        logging.getLogger("custom_components.samsung_frame_art_director").setLevel(logging.DEBUG)
        logging.getLogger("custom_components.samsung_frame_art_director.bridge").setLevel(logging.DEBUG)
        logging.getLogger("custom_components.samsung_frame_art_director.config_flow").setLevel(logging.DEBUG)
        logging.getLogger("custom_components.samsung_frame_art_director.api").setLevel(logging.DEBUG)
        # Direct module names (when imported as a package)
        logging.getLogger("samsung_frame_art_director.bridge").setLevel(logging.DEBUG)
        logging.getLogger("samsung_frame_art_director.config_flow").setLevel(logging.DEBUG)
        logging.getLogger("samsung_frame_art_director.api").setLevel(logging.DEBUG)
        logging.getLogger("samsung_frame_art_director").setLevel(logging.DEBUG)
        # Third-party lib at info
        logging.getLogger("samsungtvws").setLevel(logging.INFO)
        # samsungtvws 3.0.5 includes token values in connection INFO logs.
        logging.getLogger("samsungtvws.connection").setLevel(logging.WARNING)
        _LOGGER.info("Verbose logging enabled for Samsung Frame Art Director (debug) and samsungtvws (info)")
    except Exception:  # noqa: BLE001
        # Best effort; logging config is managed by HA logger integration normally
        pass


def _validate_remote_image_url(hass: HomeAssistant, url: str) -> None:
    """Reject unsafe remote artwork URLs before they reach the HTTP client."""
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
    except ValueError as err:
        raise ServiceValidationError("Remote image URL is invalid") from err

    if scheme not in ("http", "https"):
        raise ServiceValidationError("Unsupported image URL scheme; use HTTP or HTTPS")
    if not hostname:
        raise ServiceValidationError("Remote image URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ServiceValidationError("Remote image URL must not include credentials")
    if not hass.config.is_allowed_external_url(url):
        raise ServiceValidationError("Remote image URL is not trusted; add it to Home Assistant's allowlist_external_urls")


async def _async_read_image_bytes(hass: HomeAssistant, path: str) -> bytes:
    """Return the image bytes to upload, from an http(s) URL or a local path.

    A render host can live elsewhere on the LAN (e.g. a Mac drawing a dashboard),
    so a trusted ``http(s)://`` URL is fetched via HA's shared aiohttp client
    with a 30-second timeout and 20 MiB limit. It never has to be written to this
    host's filesystem first. Local paths keep the existing ``/media``/``/config``
    sandboxing and are read off-loop in an executor.
    """
    from urllib.parse import urljoin, urlsplit

    parsed_scheme = urlsplit(path).scheme.lower()
    if parsed_scheme in ("http", "https"):
        from aiohttp import ClientTimeout
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        _validate_remote_image_url(hass, path)
        session = async_get_clientsession(hass)
        timeout = ClientTimeout(total=30)
        current_url = path
        redirect_statuses = {301, 302, 303, 307, 308}

        try:
            async with asyncio.timeout(30):
                for redirect_count in range(MAX_REMOTE_REDIRECTS + 1):
                    async with session.get(
                        current_url,
                        timeout=timeout,
                        allow_redirects=False,
                    ) as resp:
                        if resp.status in redirect_statuses:
                            location = resp.headers.get("Location")
                            if not location:
                                raise ServiceValidationError("Remote image redirect is missing a destination")
                            if redirect_count >= MAX_REMOTE_REDIRECTS:
                                raise ServiceValidationError("Remote image exceeded the redirect limit")
                            current_url = urljoin(current_url, location)
                            _validate_remote_image_url(hass, current_url)
                            continue

                        _remote_filename(current_url)
                        resp.raise_for_status()
                        if resp.content_length is not None and resp.content_length > MAX_REMOTE_IMAGE_BYTES:
                            raise ServiceValidationError("Remote image exceeds the 20 MiB limit")
                        image_bytes = bytearray()
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            if len(image_bytes) + len(chunk) > MAX_REMOTE_IMAGE_BYTES:
                                raise ServiceValidationError("Remote image exceeds the 20 MiB limit")
                            image_bytes.extend(chunk)
                        return bytes(image_bytes)
        except TimeoutError as err:
            raise ServiceValidationError("Remote image download timed out after 30 seconds") from err

    if parsed_scheme and "://" in path:
        raise ServiceValidationError("Unsupported image URL scheme; use HTTP or HTTPS")

    def _read() -> bytes:
        try:
            resolved = resolve_upload_source(hass, path)
        except UnsafeLocalPathError as err:
            raise ServiceValidationError(str(err)) from err
        if not resolved.is_file():
            raise ServiceValidationError("Local artwork file does not exist")
        with resolved.open("rb") as f:
            return f.read()

    return await hass.async_add_executor_job(_read)


def _remote_filename(path: str) -> str:
    """Derive the basename to track on the TV from a local path or URL.

    ``urlsplit().path`` drops any ``?cache-bust`` query from URLs; for a plain
    local path ``urlsplit(path).path == path``, so this is a no-op there.
    """
    from os.path import basename
    from urllib.parse import urlsplit

    filename = basename(urlsplit(path).path)
    if not filename:
        raise ServiceValidationError("Image source must include a filename")
    return filename


def _register_domain_websocket(hass: HomeAssistant) -> None:
    """Register the Gallery WebSocket command once for the integration."""
    from homeassistant.components import websocket_api

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/get_library",
            vol.Optional("config_entry_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_get_library(hass, connection, msg):
        targets = loaded_frame_targets(hass)
        config_entry_id = msg.get("config_entry_id")
        if config_entry_id:
            target = loaded_frame_target(hass, config_entry_id)
            if target is None:
                connection.send_error(
                    msg["id"],
                    "frame_not_loaded",
                    "The selected Samsung Frame is not loaded",
                )
                return
            targets = [target]
        elif len(targets) != 1:
            connection.send_error(
                msg["id"],
                "target_required",
                "config_entry_id is required unless exactly one Samsung Frame is loaded",
            )
            return

        target = targets[0]
        data = await target.runtime.client.async_get_library_data()
        from .media_source import signed_thumbnail_url

        for item in data.get("items", []):
            item["thumbnail"] = signed_thumbnail_url(hass, target.entry.entry_id, item["id"])
        connection.send_result(msg["id"], data)

    websocket_api.async_register_command(hass, websocket_get_library)


def _register_domain_actions(hass: HomeAssistant) -> None:
    """Register action handlers shared by every loaded Frame."""

    async def _svc_set_artmode(call: ServiceCall) -> None:
        enabled = bool(call.data.get("enabled"))
        _LOGGER.debug("Action set_artmode called: enabled=%s, data=%s", enabled, dict(call.data))
        targets = await async_resolve_action_targets(hass, call)
        for target in targets:
            client = target.runtime.client
            opts = target.entry.options
            _LOGGER.debug("set_artmode: invoking client on host=%s", getattr(client, "host", "?"))
            try:
                if enabled and opts and opts.get("use_wol_before_on"):
                    mac = opts.get("mac_address")
                    if mac:
                        try:
                            # Also try the common /24 broadcast candidate derived
                            # from the TV's IP (e.g. .61 -> .255). The global
                            # broadcast remains the portable fallback because
                            # the TV does not expose its subnet mask here.
                            bcasts = []
                            host_ip = getattr(client, "host", None)
                            if host_ip and host_ip.count(".") == 3:
                                bcasts.append(host_ip.rsplit(".", 1)[0] + ".255")
                            await hass.async_add_executor_job(_send_magic_packet, mac, bcasts)
                            _LOGGER.debug("Sent WoL to %s (broadcasts=%s), sleeping before Art ON", mac, bcasts)
                            await asyncio.sleep(3)
                        except Exception as wol_err:  # noqa: BLE001
                            _LOGGER.warning("WoL send to %s failed: %r", mac, wol_err)
                await client.async_set_artmode(enabled)
                if enabled and opts and opts.get("use_power_key_on_off"):
                    # A fully powered-off Frame accepts set_artmode over the art
                    # channel but won't physically light the panel. If it still
                    # reports off, send the POWER key to wake it, then re-assert
                    # Art Mode so it lands on art rather than live TV.
                    status = await client.async_get_artmode_status()
                    if status in ("off", "false", "0", "none"):
                        _LOGGER.debug("ON wake: TV still off; sending POWER key to wake")
                        try:
                            await client.async_send_key("KEY_POWER")
                            await asyncio.sleep(3)
                            await client.async_set_artmode(True)
                        except Exception:  # noqa: BLE001
                            _LOGGER.debug("ON wake: POWER key path unavailable")
                if not enabled and opts and opts.get("use_power_key_on_off"):
                    # Re-check quickly; if still on, attempt POWER key once
                    status = await client.async_get_artmode_status()
                    if status in ("on", "true", "1"):
                        _LOGGER.debug("OFF fallback: sending POWER key via websocket remote")
                        try:
                            # Use the client's identified connection (name + token)
                            # so this does not trigger a TV authorization popup.
                            await client.async_send_key("KEY_POWER")
                            await asyncio.sleep(1.5)
                        except Exception:  # noqa: BLE001
                            _LOGGER.debug("OFF fallback: POWER key path unavailable")
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("set_artmode error on host=%s: %r", getattr(client, "host", "?"), err)

    async def _svc_upload_art(call: ServiceCall) -> dict | None:
        path = call.data.get("path")
        tags = call.data.get("tags")
        requested_matte = call.data.get("matte")
        if not path:
            return
        _LOGGER.debug(
            "Action upload_art called: path=%s matte=%s tags=%s",
            path,
            requested_matte,
            tags,
        )
        targets = await async_resolve_action_targets(hass, call)

        if is_local_media_identifier(path):
            artwork = None
            for target in targets:
                if artwork := await target.runtime.client.async_read_local_art(path):
                    break
            if not artwork:
                raise ServiceValidationError("Artwork is not in the tracked local library")
            image_bytes = artwork["data"]
            source_file = artwork["path"]
        else:
            # Preserve the established validation that a source has a usable name.
            _remote_filename(path)
            # HTTP(S) is streamed with limits; a local path is sandboxed and
            # read off-loop. Existing path/URL calls remain compatible.
            image_bytes = await _async_read_image_bytes(hass, path)
            source_file = path

        content_ids: list[str] = []
        for target in targets:
            client = target.runtime.client
            matte = requested_matte or resolve_matte(target.entry.options)
            _LOGGER.debug("upload_art: invoking client on host=%s", getattr(client, "host", "?"))
            try:
                content_id = await client.async_upload_image(
                    image_bytes,
                    matte=matte,
                    source_file=source_file,
                    tags=tags,
                )
                if content_id:
                    content_ids.append(str(content_id))

                # Run automatic cleanup (defaults from const)
                # We do this asynchronously to not block the service return too long,
                # though here we await it for simplicity as the user expects "done" state.
                # If performance is an issue, we could fire a task.
                await client.async_cleanup_storage(**_cleanup_params(target.entry))

            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("upload_art failed on host=%s: %r", getattr(client, "host", "?"), err)
        if call.return_response:
            return {
                "content_id": content_ids[0] if len(content_ids) == 1 else None,
                "content_ids": content_ids,
            }
        return None

    # Schema for services
    hass.services.async_register(
        DOMAIN,
        "set_artmode",
        _svc_set_artmode,
        schema=vol.Schema({vol.Required("enabled"): bool, vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list)}),
    )

    async def _svc_send_key(call: ServiceCall) -> None:
        targets = await async_resolve_action_targets(hass, call)
        key = call.data["key"]
        hold_seconds = call.data.get("hold_seconds")
        for target in targets:
            await target.runtime.client.async_send_key(key, hold_seconds=hold_seconds)

    hass.services.async_register(
        DOMAIN,
        "send_key",
        _svc_send_key,
        schema=vol.Schema(
            {
                vol.Required("key"): vol.All(str, vol.Match(r"^KEY_[A-Z0-9_]+$")),
                vol.Optional("hold_seconds"): vol.All(
                    vol.Coerce(float),
                    vol.Range(
                        min=MIN_KEY_HOLD_SECONDS,
                        max=MAX_KEY_HOLD_SECONDS,
                    ),
                ),
                vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list),
            }
        ),
    )

    async def _svc_ip_control(call: ServiceCall) -> None:
        targets = await async_resolve_action_targets(hass, call)
        for target in targets:
            await async_execute_ip_control_action(
                hass,
                target,
                call.service,
            )

    for action in IP_CONTROL_ACTIONS:
        hass.services.async_register(
            DOMAIN,
            action,
            _svc_ip_control,
            schema=vol.Schema({vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list)}),
        )

    hass.services.async_register(
        DOMAIN,
        "upload_art",
        _svc_upload_art,
        schema=vol.Schema(
            {
                vol.Required("path"): str,
                vol.Optional("matte"): str,
                vol.Optional("tags"): str,
                vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _svc_art_diagnostics(call: ServiceCall) -> None:
        targets = await async_resolve_action_targets(hass, call)
        for target in targets:
            await target.runtime.client.async_art_diagnostics()

    hass.services.async_register(
        DOMAIN,
        "art_diagnostics",
        _svc_art_diagnostics,
        schema=vol.Schema({vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list)}),
    )

    async def _svc_rotate_art_now(call: ServiceCall) -> None:
        tags = call.data.get("tags")
        match_all = call.data.get("match_all", False)
        source = call.data.get("source", "library")
        requested_path = call.data.get("path")

        _LOGGER.debug("Action rotate_art_now called: tags=%s match_all=%s source=%s path=%s", tags, match_all, source, requested_path)

        tag_list = [t.strip() for t in tags.split(",")] if tags else None

        targets = await async_resolve_action_targets(hass, call)
        for target in targets:
            client = target.runtime.client
            matte = resolve_matte(target.entry.options)
            try:
                if source == "folder":
                    path = requested_path or target.entry.options.get(CONF_LIBRARY_DIR) or DEFAULT_LIBRARY_DIR
                    success = await client.async_rotate_from_folder(path, matte=matte)
                    if success:
                        _LOGGER.info("rotate_art_now(folder) success on host=%s", getattr(client, "host", "?"))
                    else:
                        _LOGGER.warning("rotate_art_now(folder) failed on host=%s", getattr(client, "host", "?"))
                else:
                    success = await client.async_rotate_art(tags=tag_list, match_all=match_all, matte=matte)
                    if success:
                        _LOGGER.info("rotate_art_now(library) success on host=%s", getattr(client, "host", "?"))
                    else:
                        _LOGGER.warning(
                            "rotate_art_now(library) found no matches on host=%s for tags=%s", getattr(client, "host", "?"), tags
                        )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("rotate_art_now failed on host=%s: %r", getattr(client, "host", "?"), err)

    hass.services.async_register(
        DOMAIN,
        "rotate_art_now",
        _svc_rotate_art_now,
        schema=vol.Schema(
            {
                vol.Optional("tags"): str,
                vol.Optional("match_all"): bool,
                vol.Optional("source"): vol.In(["library", "folder"]),
                vol.Optional("path"): str,
                vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list),
            }
        ),
    )

    async def _svc_cleanup_storage(call: ServiceCall) -> None:
        targets = await async_resolve_action_targets(hass, call)
        for target in targets:
            client = target.runtime.client
            params = _cleanup_params(target.entry, call.data)
            _LOGGER.debug(
                "Action cleanup_storage called for host=%s: %s",
                getattr(client, "host", "?"),
                params,
            )
            try:
                summary = await client.async_cleanup_storage(**params)
                _LOGGER.info("cleanup_storage summary on %s: %s", getattr(client, "host", "?"), summary)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("cleanup_storage failed on host=%s: %r", getattr(client, "host", "?"), err)

    hass.services.async_register(
        DOMAIN,
        "cleanup_storage",
        _svc_cleanup_storage,
        schema=vol.Schema(
            {
                vol.Optional("max_items"): int,
                vol.Optional("max_age_days"): int,
                vol.Optional("preserve_current"): bool,
                vol.Optional("only_integration_managed"): bool,
                vol.Optional("dry_run"): bool,
                vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list),
            }
        ),
    )

    # Register Services
    async def async_service_handler(call: ServiceCall) -> None:
        """Handle service calls."""
        targets = await async_resolve_action_targets(hass, call)
        for target in targets:
            client = target.runtime.client
            if call.service == "process_inbox":
                from .curator import ContentCurator

                curator = ContentCurator(hass, target.entry, client)
                result = await curator.async_process_inbox()

                if result.get("error"):
                    persistent_notification.async_create(
                        hass,
                        f"Inbox processing stopped: {result['error']} ({result['count']} processed, {result.get('skipped', 0)} skipped).",
                        title="Art Director",
                    )
                else:
                    persistent_notification.async_create(
                        hass,
                        f"Processed {result['count']} images from Inbox; {result.get('skipped', 0)} skipped.",
                        title="Art Director",
                    )
            elif call.service == "sync_library":
                from .curator import ContentCurator

                curator = ContentCurator(hass, target.entry, client)
                result = await curator.async_sync_library()

                if result.get("error"):
                    persistent_notification.async_create(
                        hass,
                        f"Library sync stopped: {result['error']} ({result['added']} added, {result.get('skipped', 0)} skipped).",
                        title="Art Director",
                    )
                else:
                    duplicates = result["duplicates_removed"]
                    warning = f" Warning: {result['warning']}" if result.get("warning") else ""
                    persistent_notification.async_create(
                        hass,
                        f"Library sync complete: {result['added']} added, "
                        f"{result.get('skipped', 0)} skipped, "
                        f"{result['stale_removed']} stale removed, "
                        f"{duplicates} "
                        f"{'duplicate' if duplicates == 1 else 'duplicates'} removed."
                        f"{warning}",
                        title="Art Director",
                    )
            elif call.service == "purge_database":
                await client.async_purge_database()
                persistent_notification.async_create(
                    hass,
                    "Database purged successfully. Art history and local tags have been cleared.",
                    title="Art Director",
                )

    hass.services.async_register(DOMAIN, "process_inbox", async_service_handler)
    hass.services.async_register(DOMAIN, "sync_library", async_service_handler)
    hass.services.async_register(DOMAIN, "purge_database", async_service_handler)

    # New Favorites Services
    async def async_fav_handler(call: ServiceCall) -> None:
        targets = await async_resolve_action_targets(hass, call)
        for target in targets:
            client = target.runtime.client
            if call.service == "toggle_favorite":
                content_id = call.data.get("content_id")
                if not content_id:
                    try:
                        current = await client.async_get_current_art()
                        content_id = current.get("content_id")
                    except Exception:  # noqa: BLE001
                        content_id = None
                if content_id:
                    new_state = await client.async_toggle_favorite(content_id)
                    _LOGGER.debug(
                        "Toggled favorite for %s on host=%s: %s",
                        content_id,
                        getattr(client, "host", "?"),
                        new_state,
                    )
                    persistent_notification.async_create(
                        hass,
                        f"{'Added to' if new_state else 'Removed from'} favorites: {content_id}",
                        title="Art Director",
                    )
                else:
                    _LOGGER.warning("toggle_favorite: no content_id provided and no current artwork detected")
            elif call.service == "delete_art":
                content_id = call.data.get("content_id")
                if content_id:
                    success = await client.async_delete_art(content_id)
                    if success:
                        persistent_notification.async_create(
                            hass,
                            f"Deleted 1 item ({content_id}) from library.",
                            title="Art Director",
                        )
                    else:
                        raise ServiceValidationError("Artwork is not a tracked local artwork or could not be deleted")
            elif call.service == "rotate_favorites":
                matte = resolve_matte(target.entry.options)
                await client.async_rotate_art(source="favorites", matte=matte)

    hass.services.async_register(DOMAIN, "toggle_favorite", async_fav_handler)
    hass.services.async_register(DOMAIN, "delete_art", async_fav_handler)
    hass.services.async_register(DOMAIN, "rotate_favorites", async_fav_handler)

    # Service to change gallery page (Avoiding Jinja in frontend tap_action)
    async def async_change_page(call: ServiceCall) -> None:
        step = call.data.get("step", 0)
        for target in await async_resolve_action_targets(hass, call):
            page_entity_id = entry_entity_id(hass, target.entry, "number", "gallery_page")
            library_entity_id = entry_entity_id(hass, target.entry, "sensor", "art_library")
            page_state = hass.states.get(page_entity_id) if page_entity_id else None
            library_state = hass.states.get(library_entity_id) if library_entity_id else None

            total_items = 0
            if library_state and library_state.state not in (
                "unknown",
                "unavailable",
            ):
                try:
                    total_items = int(library_state.state)
                except ValueError:
                    pass

            page_size = 25
            max_page = max(1, (total_items + page_size - 1) // page_size)
            if page_state and page_state.state not in ("unknown", "unavailable"):
                try:
                    current = int(float(page_state.state))
                    new_val = max(1, min(max_page, current + step))
                    await hass.services.async_call(
                        "number",
                        "set_value",
                        {"entity_id": page_entity_id, "value": new_val},
                        blocking=False,
                    )
                except ValueError:
                    pass

    hass.services.async_register(DOMAIN, "change_gallery_page", async_change_page)


async def async_setup_entry(hass: HomeAssistant, entry: SamsungFrameConfigEntry) -> bool:
    """Set up Samsung Frame Art Director from a config entry."""
    _LOGGER.info("Setting up Samsung Frame Art Director for host=%s", entry.data.get("host"))

    # Import here to avoid blocking config_flow import on package import
    from .api import AuthenticationRejectedError, SamsungFrameClient

    # Enable verbose logs from the beginning for diagnostics
    _enable_verbose_logging()

    # Compatibility Patch: fix missing is_true in samsungtvws.helper
    try:
        import samsungtvws.helper as _helper

        if not hasattr(_helper, "is_true"):
            _LOGGER.debug("Patching samsungtvws.helper.is_true")
            _helper.is_true = lambda val: str(val).lower() in ("true", "1", "on", "yes")
    except Exception:
        pass

    # Ensure /config/deps is on sys.path so HA can see manually installed deps
    try:
        import os as _os
        import sys as _sys

        deps_base = hass.config.path("deps")
        candidates = [
            deps_base,
            _os.path.join(deps_base, f"lib/python{_sys.version_info.major}.{_sys.version_info.minor}/site-packages"),
        ]
        for cand in candidates:
            if _os.path.isdir(cand) and cand not in _sys.path:
                _sys.path.insert(0, cand)
                _LOGGER.debug("Added to sys.path: %s", cand)
    except Exception:  # noqa: BLE001
        pass

    # Log samsungtvws version and whether async_art is available
    try:
        import samsungtvws  # type: ignore

        ver = getattr(samsungtvws, "__version__", "unknown")
        _LOGGER.info("samsungtvws package version: %s", ver)
    except Exception as e:  # noqa: BLE001
        _LOGGER.info("samsungtvws package not importable: %r", e)

    # Respect diagnostics verbosity option (off by default)
    try:
        if entry.options.get("diagnostics_verbose", False):
            _enable_verbose_logging()
    except Exception:  # noqa: BLE001
        pass

    # Best-effort: create the inbox/library folders so users can drop images
    # immediately without first running a service.
    try:
        import os as _os

        for _d in (
            entry.options.get(CONF_INBOX_DIR) or DEFAULT_INBOX_DIR,
            entry.options.get(CONF_LIBRARY_DIR) or DEFAULT_LIBRARY_DIR,
        ):
            _os.makedirs(_d, exist_ok=True)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not pre-create media folders", exc_info=True)

    # Initialize with the persisted ConfigEntry identity. The pairing file path
    # is retained only so an obsolete config-flow token file can be removed
    # after authenticated startup succeeds.
    host = entry.data.get("host")
    safe_host = str(host).replace("/", "_").replace(".", "_")
    token_file_path = hass.config.path(f"pairing_tokens/token_{safe_host}.txt")
    client = SamsungFrameClient(hass, host, entry.data.get("token"), token_file_path=token_file_path, port=entry.data.get("port"))

    # Persist a refreshed token whenever the TV (re)issues one during normal
    # operation, so authorization stays valid across reconnects and the TV
    # stops re-prompting for access. Called from worker threads, so hop back
    # onto the event loop before touching the config entry.
    def _persist_token(new_token: str) -> None:
        def _update() -> None:
            cur = hass.config_entries.async_get_entry(entry.entry_id)
            if cur and new_token and new_token != cur.data.get("token"):
                _LOGGER.info("Persisting refreshed token for host=%s", host)
                hass.config_entries.async_update_entry(cur, data={**cur.data, "token": new_token})

        hass.loop.call_soon_threadsafe(_update)

    client.set_token_persister(_persist_token)
    client.set_resize_mode(entry.options.get(CONF_RESIZE_MODE, DEFAULT_RESIZE_MODE))

    # Provide DB path for cleanup service (directory may not exist yet)
    try:
        entry_db_path, local_db_path = await async_prepare_entry_database(hass, entry.entry_id)
        client.set_db_path(entry_db_path)
        client.set_local_db_path(local_db_path)
        await client.async_initialize_database()
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady(f"Library database initialization failed: {err}") from err
    try:
        # Validate the saved token without opening a new pairing flow. An
        # explicit rejection, and a handshake that never completes against a
        # TV that is demonstrably reachable, both start reauth — the latter is
        # the on-screen approval dialog, which only the user can clear.
        # Genuine unreachability and missing device information remain
        # retryable setup failures.
        await client.async_connect_and_pair()
    except AuthenticationRejectedError as err:
        _LOGGER.debug("Client pairing failed (auth): %r", err, exc_info=True)
        raise ConfigEntryAuthFailed from err
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Client connect_and_pair failed: %r", err, exc_info=True)
        raise ConfigEntryNotReady from err

    # If we obtained a new token, persist it into the ConfigEntry
    if client.token and client.token != entry.data.get("token"):
        _LOGGER.info("Token updated for host=%s; persisting to ConfigEntry", entry.data.get("host"))
        new_data = {**entry.data, "token": client.token}
        hass.config_entries.async_update_entry(entry, data=new_data)

    entry.runtime_data = SamsungFrameRuntimeData(client=client)

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await _reload_slideshow_timer(hass, entry)
    except Exception:
        try:
            await client.async_disconnect()
        finally:
            entry.runtime_data = None
        raise

    # Register update listener to reload entry when options change
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: SamsungFrameConfigEntry) -> None:
    """Update options."""
    # Check if we need a full reload (e.g. if non-slideshow options changed)
    # For now, we assume most option changes are slideshow related and can be hot-reloaded.
    # If connection-critical options were in 'options', we would check them here.

    # Re-apply runtime client preferences
    entry.runtime_data.client.set_resize_mode(entry.options.get(CONF_RESIZE_MODE, DEFAULT_RESIZE_MODE))

    # Reload slideshow timer directly
    await _reload_slideshow_timer(hass, entry)

    # We do NOT request a config entry reload, which prevents the "unavailable" blip.
    # Note: If you add options that require restart (like mac address), handle them here.


async def _reload_slideshow_timer(hass: HomeAssistant, entry: SamsungFrameConfigEntry) -> None:
    """Start or stop the slideshow timer based on options."""
    runtime = entry.runtime_data

    # Cancel existing timer if any
    if runtime.timer_unsub:
        runtime.timer_unsub()
        runtime.timer_unsub = None

    interval = entry.options.get(CONF_SLIDESHOW_INTERVAL) or DEFAULT_SLIDESHOW_INTERVAL
    enabled = entry.options.get(CONF_SLIDESHOW_ENABLED, False)

    if interval > 0 and enabled:
        _LOGGER.info("Starting slideshow timer for %s every %s minutes", entry.title, interval)
        from datetime import timedelta

        from homeassistant.helpers.event import async_track_time_interval

        async def _tick(now):
            await _run_slideshow_job(hass, entry)

        runtime.timer_unsub = async_track_time_interval(hass, _tick, timedelta(minutes=interval))


async def _run_slideshow_job(hass: HomeAssistant, entry: SamsungFrameConfigEntry) -> None:
    """Pick a random image from source_dir and upload it."""
    runtime = entry.runtime_data
    client = runtime.client

    # Skip this tick if the previous slideshow upload is still running. Uploading
    # over a slow Frame connection can take longer than an aggressive interval,
    # and without this guard ticks would pile up and overwhelm the TV.
    if runtime.slideshow_running:
        _LOGGER.debug("Slideshow skipped: previous rotation still in progress")
        return
    runtime.slideshow_running = True
    try:
        await _do_slideshow_rotation(hass, entry, client)
    finally:
        runtime.slideshow_running = False


async def _do_slideshow_rotation(hass: HomeAssistant, entry: ConfigEntry, client) -> None:
    """Perform a single slideshow rotation (guarded by ``_run_slideshow_job``)."""
    # Check if TV is in Art Mode. Do not interrupt movies or wake a fully powered off TV.
    try:
        status = await client.async_get_artmode_status()
        if status not in ("on", "true", "1"):
            _LOGGER.debug("Slideshow skipped: TV is not in Art Mode (status=%s)", status)
            return
    except Exception as e:
        _LOGGER.debug("Slideshow skipped: Could not determine Art Mode status: %s", e)
        return

    source_type = entry.options.get(CONF_SLIDESHOW_SOURCE_TYPE, SLIDESHOW_SOURCE_FOLDER)
    filter_val = entry.options.get(CONF_SLIDESHOW_FILTER)
    matte = resolve_matte(entry.options)

    # --- NEW LOGIC: Respect Dashboard Filters ---
    # 1. Favorites Filter
    fav_entity_id = entry_entity_id(hass, entry, "switch", "favorites_filter")
    fav_switch = hass.states.get(fav_entity_id) if fav_entity_id else None
    fav_only = fav_switch and fav_switch.state == "on"

    # 2. Text/Tag Filter
    text_entity_id = entry_entity_id(hass, entry, "text", "slideshow_filter")
    text_filter = hass.states.get(text_entity_id) if text_entity_id else None
    tags_filter = []
    neg_filter = []

    if text_filter and text_filter.state not in (None, "unknown", "", "unavailable"):
        # Split by comma if multiple tags
        raw_tags = [t.strip() for t in text_filter.state.split(",")]
        for t in raw_tags:
            if t.startswith("-") and len(t) > 1:
                neg_filter.append(t[1:])
            elif t:
                tags_filter.append(t)

    # If any dashboard filter is active, override the default options
    if fav_only or tags_filter or neg_filter:
        _LOGGER.debug(f"Slideshow: Using Dashboard filters (Fav={fav_only}, Tags={tags_filter}, Exclude={neg_filter})")
        await client.async_rotate_art(
            tags=tags_filter, negative_tags=neg_filter, source="favorites" if fav_only else "library", matte=matte
        )
        # Cleanup and exit early (skip default logic)
        try:
            await client.async_cleanup_storage(**_cleanup_params(entry))
        except Exception:
            pass
        return
    # --------------------------------------------

    library_dir = entry.options.get(CONF_LIBRARY_DIR) or DEFAULT_LIBRARY_DIR

    # Fallback if filter is empty but path exists (legacy)
    if not filter_val and source_type == SLIDESHOW_SOURCE_FOLDER:
        filter_val = entry.options.get(CONF_SLIDESHOW_SOURCE_PATH, library_dir)

    if source_type == SLIDESHOW_SOURCE_FOLDER:
        path = filter_val or library_dir
        await client.async_rotate_from_folder(path, matte=matte)
    elif source_type == SLIDESHOW_SOURCE_TAGS:
        tags = [t.strip() for t in filter_val.split(",")] if filter_val else []
        if tags:
            await client.async_rotate_art(tags=tags, matte=matte)
        else:
            _LOGGER.warning("Slideshow: Tags source selected but no tags configured")
    else:
        # All Library
        await client.async_rotate_art(match_all=True, matte=matte)

    # Force cleanup to keep integration-managed TV storage within the configured
    # limit. Manual and Art Store images are never deletion-eligible.
    try:
        await client.async_cleanup_storage(**_cleanup_params(entry))
    except Exception as e:
        _LOGGER.warning("Slideshow cleanup failed: %s", e)


async def async_unload_entry(hass: HomeAssistant, entry: SamsungFrameConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Samsung Frame Art Director")

    runtime = entry.runtime_data
    if runtime.timer_unsub:
        runtime.timer_unsub()
        runtime.timer_unsub = None

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        try:
            await runtime.client.async_disconnect()
        finally:
            entry.runtime_data = None

    return unload_ok
