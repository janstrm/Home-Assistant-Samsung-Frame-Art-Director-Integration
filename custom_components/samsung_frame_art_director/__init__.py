"""Samsung Frame Art Director integration."""

import logging
import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er, service as ha_service
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.components import persistent_notification

from .const import (
    DATA_CLIENT,
    DOMAIN,
    CONF_SLIDESHOW_INTERVAL,
    CONF_SLIDESHOW_SOURCE_PATH,
    CONF_SLIDESHOW_ENABLED,
    CONF_SLIDESHOW_SOURCE_TYPE,
    CONF_SLIDESHOW_FILTER,
    SLIDESHOW_SOURCE_FOLDER,
    SLIDESHOW_SOURCE_TAGS,
    DEFAULT_SLIDESHOW_INTERVAL,
    CONF_RESIZE_MODE,
    DEFAULT_RESIZE_MODE,
    CONF_INBOX_DIR,
    DEFAULT_INBOX_DIR,
    CONF_LIBRARY_DIR,
    DEFAULT_LIBRARY_DIR,
    CONF_MATTE_ENABLED,
    CONF_MATTE_STYLE,
    CONF_MATTE_COLOR,
    DEFAULT_MATTE_STYLE,
    DEFAULT_MATTE_COLOR,
    MATTE_STYLE_NONE,
    resolve_matte,
)
from .const import DB_DIR, DB_FILE, DEFAULT_CLEANUP_DRY_RUN, DEFAULT_CLEANUP_ONLY_INTEGRATION_MANAGED, DEFAULT_CLEANUP_PRESERVE_CURRENT, DEFAULT_CLEANUP_MAX_ITEMS


# This integration is configured via the UI only (config entries), not YAML.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Set up via configuration.yaml (not used)."""
    return True
PLATFORMS = ["media_player", "number", "switch", "select", "text", "image", "sensor"]

_LOGGER = logging.getLogger(__name__)


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
        # Standard WoL ports (9 and 7) on every target broadcast address.
        for ip in targets:
            for port in (9, 7):
                try:
                    sock.sendto(payload, (ip, port))
                except OSError:
                    pass


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
    if (
        legacy_dir
        and legacy_dir != DEFAULT_LIBRARY_DIR
        and not new_options.get(CONF_LIBRARY_DIR)
    ):
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
        _LOGGER.info("Verbose logging enabled for Samsung Frame Art Director (debug) and samsungtvws (info)")
    except Exception:  # noqa: BLE001
        # Best effort; logging config is managed by HA logger integration normally
        pass


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Samsung Frame Art Director from a config entry."""
    _LOGGER.info("Setting up Samsung Frame Art Director for host=%s", entry.data.get("host"))

    # Import here to avoid blocking config_flow import on package import
    from .api import SamsungFrameClient, PairingTimeoutError

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
        import sys as _sys
        import os as _os
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

    # Initialize and connect client; use persistent token file under /config
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
        import os as _os
        db_dir = hass.config.path(DB_DIR)
        _os.makedirs(db_dir, exist_ok=True)
        client.set_db_path(hass.config.path(f"{DB_DIR}/{DB_FILE}"))
    except Exception:  # noqa: BLE001
        pass
    try:
        # Validate token at setup. PairingTimeoutError means the device identity
        # could not be established (no token/duid) -> trigger reauth so the user
        # can re-accept on the TV. Other failures are treated as transient.
        await client.async_connect_and_pair()
    except PairingTimeoutError as err:
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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_CLIENT: client,
        **entry.data,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register HTTP View for Thumbnails
    from .views import SamsungFrameThumbnailView
    hass.http.register_view(SamsungFrameThumbnailView(hass))

    # Register domain-level actions (a.k.a. services) that accept target entities
    async def _resolve_clients(call: ha_service.ServiceCall):
        entity_ids = await ha_service.async_extract_entity_ids(call)
        if not entity_ids:
            # If no target provided, default to this entry's client
            stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if stored:
                yield stored.get(DATA_CLIENT)
            return
        ent_reg = er.async_get(hass)
        for entity_id in entity_ids:
            ent = ent_reg.async_get(entity_id)
            config_entry_id = getattr(ent, "config_entry_id", None) if ent else None
            if config_entry_id:
                stored = hass.data.get(DOMAIN, {}).get(config_entry_id)
                if stored and (client := stored.get(DATA_CLIENT)):
                    yield client

    async def _svc_set_artmode(call: ha_service.ServiceCall) -> None:
        enabled = bool(call.data.get("enabled"))
        _LOGGER.debug("Action set_artmode called: enabled=%s, data=%s", enabled, dict(call.data))
        found = False
        async for client in _resolve_clients(call):
            found = True
            _LOGGER.debug("set_artmode: invoking client on host=%s", getattr(client, "host", "?"))
            try:
                # Options: WoL before ON, POWER key after OFF failure
                entry_id = None
                # Find the corresponding entry id for this client
                for cid, stored in hass.data.get(DOMAIN, {}).items():
                    if stored.get(DATA_CLIENT) is client:
                        entry_id = cid
                        break
                opts = None
                if entry_id:
                    entry_obj = hass.config_entries.async_get_entry(entry_id)
                    if entry_obj:
                        opts = entry_obj.options or {}
                if enabled and opts and opts.get("use_wol_before_on"):
                    mac = opts.get("mac_address")
                    if mac:
                        try:
                            # Derive the subnet-directed broadcast from the TV's
                            # IP (e.g. 192.168.68.61 -> 192.168.68.255) so the
                            # packet reaches the TV even when global broadcast
                            # isn't forwarded to a sleeping device.
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
                    if status in ("off", "false", "0", "none", None):
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
        if not found:
            _LOGGER.debug("set_artmode: no target client resolved; nothing executed")

    async def _svc_upload_art(call: ha_service.ServiceCall) -> None:
        path = call.data.get("path")
        tags = call.data.get("tags")
        # Matte from call data, else the configured style/color (or 'none').
        matte = call.data.get("matte") or resolve_matte(entry.options)
        if not path:
            return
        _LOGGER.debug("Action upload_art called: path=%s matte=%s tags=%s", path, matte, tags)
        # Read file in executor to avoid blocking
        def _read(p: str) -> bytes:
            import os
            # Accept absolute /media/... or /config/...; else assume under /media/frame/library/
            norm = os.path.expanduser(p)
            if not norm.startswith("/media/") and not norm.startswith("/config/"):
                norm = "/media/frame/library/" + norm.lstrip("/")
            
            # Security: Prevent path traversal by resolving the absolute path
            # and ensuring it's within the allowed directories
            abs_norm = os.path.abspath(norm)
            allowed_media = os.path.abspath("/media")
            allowed_config = os.path.abspath(hass.config.path())
            if not abs_norm.startswith(allowed_media) and not abs_norm.startswith(allowed_config):
                raise ValueError(f"Path traversal detected or unallowed path: {abs_norm}")
            
            _LOGGER.debug("upload_art: resolved path=%s", abs_norm)
            # Map /media to real FS under HA config; hass.config.path maps /config
            # Supervisor mounts /media; opening /media/... directly should work. Keep as-is.
            with open(abs_norm, "rb") as f:
                return f.read()

        image_bytes = await hass.async_add_executor_job(_read, path)
        found = False
        async for client in _resolve_clients(call):
            found = True
            _LOGGER.debug("upload_art: invoking client on host=%s", getattr(client, "host", "?"))
            try:
                await client.async_upload_image(image_bytes, matte=matte)
                # Track and cleanup
                # We assume the file uploaded is the basename
                # Ideally async_upload_image would return the content_id/filename it uploaded.
                from os.path import basename
                remote_filename = basename(path)
                await client.async_track_art(remote_filename, tags=tags)
                
                # Run automatic cleanup (defaults from const)
                # We do this asynchronously to not block the service return too long, 
                # though here we await it for simplicity as the user expects "done" state.
                # If performance is an issue, we could fire a task.
                await client.async_cleanup_storage(
                    max_items=DEFAULT_CLEANUP_MAX_ITEMS, 
                    only_integration_managed=DEFAULT_CLEANUP_ONLY_INTEGRATION_MANAGED,
                    preserve_current=DEFAULT_CLEANUP_PRESERVE_CURRENT
                )
                
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("upload_art failed on host=%s: %r", getattr(client, "host", "?"), err)
        if not found:
            _LOGGER.debug("upload_art: no target client resolved; nothing executed")

    # Schema for services
    hass.services.async_register(
        DOMAIN,
        "set_artmode",
        _svc_set_artmode,
        schema=vol.Schema({vol.Required("enabled"): bool, vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list)}),
    )
    hass.services.async_register(
        DOMAIN,
        "upload_art",
        _svc_upload_art,
        schema=vol.Schema({
            vol.Required("path"): str,
            vol.Optional("matte"): str,
            vol.Optional("tags"): str,
            vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list),
        }),
    )

    async def _svc_art_diagnostics(call: ha_service.ServiceCall) -> None:
        async for client in _resolve_clients(call):
            await client.async_art_diagnostics()

    hass.services.async_register(
        DOMAIN,
        "art_diagnostics",
        _svc_art_diagnostics,
        schema=vol.Schema({vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list)}),
    )

    async def _svc_rotate_art_now(call: ha_service.ServiceCall) -> None:
        tags = call.data.get("tags")
        match_all = call.data.get("match_all", False)
        source = call.data.get("source", "library")
        path = call.data.get("path")
        
        _LOGGER.debug("Action rotate_art_now called: tags=%s match_all=%s source=%s path=%s", tags, match_all, source, path)
        
        tag_list = [t.strip() for t in tags.split(",")] if tags else None

        # Get matte from configured style/color
        matte = resolve_matte(entry.options)

        async for client in _resolve_clients(call):
            try:
                if source == "folder":
                    # Use provided path or default from options
                    if not path:
                         # Try config entry options if available
                         # We need to find the entry for this client
                         # This implies we lookup the entry ID.
                         # Simplified: if path is missing, use default const
                         path = "/media/frame/library"
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
                        _LOGGER.warning("rotate_art_now(library) found no matches on host=%s for tags=%s", getattr(client, "host", "?"), tags)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("rotate_art_now failed on host=%s: %r", getattr(client, "host", "?"), err)

    hass.services.async_register(
        DOMAIN,
        "rotate_art_now",
        _svc_rotate_art_now,
        schema=vol.Schema({
            vol.Optional("tags"): str,
            vol.Optional("match_all"): bool,
            vol.Optional("source"): vol.In(["library", "folder"]),
            vol.Optional("path"): str,
            vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list),
        }),
    )

    async def _svc_cleanup_storage(call: ha_service.ServiceCall) -> None:
        params = {
            "max_items": call.data.get("max_items", entry.options.get("cleanup_max_items")),
            "max_age_days": call.data.get("max_age_days", (entry.options.get("cleanup_max_age_days") or None) ),
            "preserve_current": call.data.get("preserve_current", entry.options.get("cleanup_preserve_current", DEFAULT_CLEANUP_PRESERVE_CURRENT)),
            "only_integration_managed": call.data.get("only_integration_managed", entry.options.get("cleanup_only_integration_managed", DEFAULT_CLEANUP_ONLY_INTEGRATION_MANAGED)),
            "dry_run": call.data.get("dry_run", entry.options.get("cleanup_dry_run", DEFAULT_CLEANUP_DRY_RUN)),
        }
        _LOGGER.debug("Action cleanup_storage called: %s", params)
        async for client in _resolve_clients(call):
            try:
                summary = await client.async_cleanup_storage(**params)
                _LOGGER.info("cleanup_storage summary on %s: %s", getattr(client, "host", "?"), summary)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("cleanup_storage failed on host=%s: %r", getattr(client, "host", "?"), err)

    hass.services.async_register(
        DOMAIN,
        "cleanup_storage",
        _svc_cleanup_storage,
        schema=vol.Schema({
            vol.Optional("max_items"): int,
            vol.Optional("max_age_days"): int,
            vol.Optional("preserve_current"): bool,
            vol.Optional("only_integration_managed"): bool,
            vol.Optional("dry_run"): bool,
            vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list),
        }),
    )

    # Setup slideshow timer if configured
    await _reload_slideshow_timer(hass, entry)

    # Register Services
    async def async_service_handler(call: ServiceCall) -> None:
        """Handle service calls."""
        if call.service == "process_inbox":
            from .curator import ContentCurator
            stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if not stored:
                return
            client = stored.get(DATA_CLIENT)
            curator = ContentCurator(hass, entry, client)
            result = await curator.async_process_inbox()
            
            if result.get("error"):
                persistent_notification.async_create(
                    hass,
                    f"Inbox Processing Failed: {result['error']}",
                    title="Art Director"
                )
            else:
                persistent_notification.async_create(
                    hass,
                    f"Processed {result['count']} images from Inbox.",
                    title="Art Director"
                )
            return

        elif call.service == "sync_library":
            from .curator import ContentCurator
            stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if not stored:
                return
            client = stored.get(DATA_CLIENT)
            curator = ContentCurator(hass, entry, client)
            result = await curator.async_sync_library()
            
            if result.get("error"):
                persistent_notification.async_create(
                    hass,
                    f"Library Sync Failed: {result['error']}",
                    title="Art Director"
                )
            else:
                persistent_notification.async_create(
                    hass,
                    f"Synced {result['count']} untracked images to the database.",
                    title="Art Director"
                )
            return

        elif call.service == "purge_database":
            stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if not stored:
                return
            client = stored.get(DATA_CLIENT)
            if client:
                await client.async_purge_database()
                persistent_notification.async_create(
                    hass,
                    "Database purged successfully. Art history and local tags have been cleared.",
                    title="Art Director"
                )
            return

    hass.services.async_register(DOMAIN, "process_inbox", async_service_handler)
    hass.services.async_register(DOMAIN, "sync_library", async_service_handler)
    hass.services.async_register(DOMAIN, "purge_database", async_service_handler)

    # New Favorites Services
    async def async_fav_handler(call: ServiceCall) -> None:
        stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not stored: return
        client = stored.get(DATA_CLIENT)
        if not client: return
        
        elif call.service == "toggle_favorite":
            content_id = call.data.get("content_id")
            if not content_id:
                # Default to whatever is currently displayed on the TV.
                try:
                    current = await client.async_get_current_art()
                    content_id = current.get("content_id")
                except Exception:  # noqa: BLE001
                    content_id = None
            if content_id:
                new_state = await client.async_toggle_favorite(content_id)
                _LOGGER.debug(f"Toggled favorite for {content_id}: {new_state}")
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
                        title="Art Director"
                    )
                
        elif call.service == "rotate_favorites":
            matte = resolve_matte(entry.options)
            await client.async_rotate_art(source="favorites", matte=matte)
            
    hass.services.async_register(DOMAIN, "toggle_favorite", async_fav_handler)
    hass.services.async_register(DOMAIN, "delete_art", async_fav_handler)
    hass.services.async_register(DOMAIN, "rotate_favorites", async_fav_handler)

    # Service to change gallery page (Avoiding Jinja in frontend tap_action)
    async def async_change_page(call: ServiceCall) -> None:
        step = call.data.get("step", 0)
        entity_id = "number.samsung_frame_gallery_page"
        state = hass.states.get(entity_id)
        
        # Get total items to calculate max page
        lib_sensor = hass.states.get("sensor.samsung_frame_art_library")
        total_items = 0
        if lib_sensor and lib_sensor.state not in ("unknown", "unavailable"):
            try:
                total_items = int(lib_sensor.state)
            except ValueError:
                pass
        
        page_size = 25
        max_page = max(1, (total_items + page_size - 1) // page_size)

        if state and state.state not in ("unknown", "unavailable"):
            try:
                current = int(float(state.state))
                new_val = max(1, min(max_page, current + step))
                await hass.services.async_call(
                    "number", 
                    "set_value", 
                    {"entity_id": entity_id, "value": new_val},
                    blocking=False
                )
            except ValueError:
                pass
    
    hass.services.async_register(DOMAIN, "change_gallery_page", async_change_page)

    # Register WebSocket API for Gallery Dashboard
    from homeassistant.components import websocket_api
    @websocket_api.websocket_command({
        "type": f"{DOMAIN}/get_library"
    })
    @websocket_api.async_response
    async def websocket_get_library(hass, connection, msg):
        stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not stored:
             connection.send_result(msg["id"], {"items": []})
             return
        client = stored.get(DATA_CLIENT)
        data = await client.async_get_library_data()
        connection.send_result(msg["id"], data)
        
    try:
        websocket_api.async_register_command(hass, websocket_get_library)
    except Exception:
        # Already registered
        pass
    
    # Register update listener to reload entry when options change
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    # Check if we need a full reload (e.g. if non-slideshow options changed)
    # For now, we assume most option changes are slideshow related and can be hot-reloaded.
    # If connection-critical options were in 'options', we would check them here.
    
    # Re-apply runtime client preferences
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data and (client := data.get(DATA_CLIENT)):
        client.set_resize_mode(entry.options.get(CONF_RESIZE_MODE, DEFAULT_RESIZE_MODE))

    # Reload slideshow timer directly
    await _reload_slideshow_timer(hass, entry)
    
    # We do NOT request a config entry reload, which prevents the "unavailable" blip.
    # Note: If you add options that require restart (like mac address), handle them here.


async def _reload_slideshow_timer(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start or stop the slideshow timer based on options."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return

    # Cancel existing timer if any
    if "timer_unsub" in data:
        data["timer_unsub"]()
        data.pop("timer_unsub")

    interval = entry.options.get(CONF_SLIDESHOW_INTERVAL) or DEFAULT_SLIDESHOW_INTERVAL
    enabled = entry.options.get(CONF_SLIDESHOW_ENABLED, False)
    
    if interval > 0 and enabled:
        _LOGGER.info("Starting slideshow timer for %s every %s minutes", entry.title, interval)
        from homeassistant.helpers.event import async_track_time_interval
        from datetime import timedelta

        async def _tick(now):
            await _run_slideshow_job(hass, entry)

        data["timer_unsub"] = async_track_time_interval(hass, _tick, timedelta(minutes=interval))


async def _run_slideshow_job(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Pick a random image from source_dir and upload it."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return
    client = data.get(DATA_CLIENT)
    if not client:
        return

    # Skip this tick if the previous slideshow upload is still running. Uploading
    # over a slow Frame connection can take longer than an aggressive interval,
    # and without this guard ticks would pile up and overwhelm the TV.
    if data.get("slideshow_running"):
        _LOGGER.debug("Slideshow skipped: previous rotation still in progress")
        return
    data["slideshow_running"] = True
    try:
        await _do_slideshow_rotation(hass, entry, client)
    finally:
        data["slideshow_running"] = False


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
    fav_switch = hass.states.get("switch.samsung_frame_gallery_favorites_only")
    fav_only = fav_switch and fav_switch.state == "on"

    # 2. Text/Tag Filter
    text_filter = hass.states.get("text.samsung_frame_slideshow_filter")
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
            tags=tags_filter,
            negative_tags=neg_filter,
            source="favorites" if fav_only else "library",
            matte=matte
        )
        # Cleanup and exit early (skip default logic)
        cleanup_max = entry.options.get("cleanup_max_items", DEFAULT_CLEANUP_MAX_ITEMS)
        try:
            await client.async_cleanup_storage(max_items=cleanup_max, only_integration_managed=False)
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

    # Force cleanup to keep only the configured max images (default 50)
    # We disable "only_integration_managed" to allow cleaning up old/untracked images
    cleanup_max = entry.options.get("cleanup_max_items", DEFAULT_CLEANUP_MAX_ITEMS)
    try:
        await client.async_cleanup_storage(max_items=cleanup_max, only_integration_managed=False)
    except Exception as e:
        _LOGGER.warning("Slideshow cleanup failed: %s", e)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Samsung Frame Art Director")
    
    # Cancel timer
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data and "timer_unsub" in data:
        data["timer_unsub"]()
        data.pop("timer_unsub")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Disconnect client
        stored = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if stored and (client := stored.get(DATA_CLIENT)):
            await client.async_disconnect()

    return unload_ok


