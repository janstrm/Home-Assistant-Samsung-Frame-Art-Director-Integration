"""Async wrapper for samsungtvws client used by the integration.

This wrapper encapsulates connection, pairing (token), DUID retrieval,
and basic Art Mode controls in async methods compatible with Home Assistant.
"""

from __future__ import annotations

import asyncio
import logging
import os

from .const import DOMAIN
from .file_access import (
    UnsafeLocalPathError,
    ensure_allowed_local_path,
    image_content_type,
    is_local_media_identifier,
    media_identifier,
    resolve_upload_source,
)
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Suppress local HTTPS cert warnings from TV endpoints during pairing/info calls
try:  # pragma: no cover - best-effort suppression
    import urllib3  # type: ignore

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

_LOGGER = logging.getLogger(__name__)

CONNECTION_ATTEMPT_TIMEOUT_SECONDS = 10
ART_OPERATION_TIMEOUT_SECONDS = 15


def _local_art_path_for_media_id(conn, media_id: str) -> str | None:
    """Resolve an opaque local-art ID through the tracked database records."""
    if not is_local_media_identifier(media_id):
        return None
    rows = conn.execute("SELECT file_path FROM local_art").fetchall()
    return next(
        (file_path for (file_path,) in rows if media_identifier(file_path) == media_id),
        None,
    )


def _canonical_source_identity(hass: HomeAssistant, source: str) -> str:
    """Return a stable identity for harmless aliases of one artwork source."""
    import posixpath
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(source)
    scheme = parsed.scheme.lower()
    if scheme in ("http", "https"):
        hostname = (parsed.hostname or "").lower()
        if ":" in hostname:
            hostname = f"[{hostname}]"
        port = parsed.port
        if port and not (
            (scheme == "http" and port == 80)
            or (scheme == "https" and port == 443)
        ):
            hostname = f"{hostname}:{port}"
        path = posixpath.normpath(parsed.path or "/")
        if parsed.path.endswith("/") and not path.endswith("/"):
            path += "/"
        return urlunsplit((scheme, hostname, path, parsed.query, ""))
    try:
        return str(resolve_upload_source(hass, source))
    except UnsafeLocalPathError:
        return os.path.realpath(os.path.expanduser(source))


class AuthenticationRejectedError(Exception):
    """Raised when the TV explicitly rejects the persisted identity."""


class DeviceUnavailableError(ConnectionError):
    """Raised when startup validation cannot reach a usable TV endpoint."""


class PairingTimeoutError(AuthenticationRejectedError):
    """Backward-compatible name for the former setup authentication error."""


class SamsungFrameClient:
    """Thin async client facade for Samsung TV WS API."""

    def __init__(self, hass: HomeAssistant, host: str, token: Optional[str] = None, token_file_path: Optional[str] = None, port: Optional[int] = None) -> None:
        self.hass = hass
        self._host = host
        self._token = token
        self._connected = False
        self._duid: Optional[str] = None
        self._client_name = "Home Assistant Art Director"
        self._token_file_path = token_file_path
        self._port: Optional[int] = port
        # Serialize art channel operations to avoid contention (upload vs set_artmode, etc.)
        self._art_lock: asyncio.Lock = asyncio.Lock()
        # DB path (set on demand by caller)
        self._db_path: Optional[str] = None
        # Loop-safe callback to persist a refreshed token (set by the integration)
        self._token_persister = None
        # Image preprocessing preference: "crop" (center-crop) or "fit" (letterbox)
        self._resize_mode = "crop"

    def set_db_path(self, path: str) -> None:
        self._db_path = path

    def set_resize_mode(self, mode: str) -> None:
        """Set image preprocessing mode: 'crop' (center-crop) or 'fit' (pad)."""
        self._resize_mode = "fit" if str(mode).lower() == "fit" else "crop"

    def set_token_persister(self, persister) -> None:
        """Register a callback used to persist a refreshed token.

        The callback is invoked from worker threads, so it must be loop-safe
        (the integration schedules the config-entry update via
        ``loop.call_soon_threadsafe``).
        """
        self._token_persister = persister

    def _make_tv(
        self,
        port: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        """Create a sync SamsungTVWS client that ALWAYS identifies with our
        client name and token (when known).

        The Frame ties authorization to the (name, token) pair. Connecting
        without them — or under a different name — makes the TV treat us as a
        new device and pop the "Allow access" dialog again, which is the root
        cause of recurring pairing prompts. Routing every connection through
        here guarantees a stable identity.
        """
        from samsungtvws import SamsungTVWS  # type: ignore

        kwargs = {
            "port": port or self._port or 8002,
            "name": self._client_name,
        }
        if self._token:
            kwargs["token"] = self._token
        if timeout is not None:
            kwargs["timeout"] = timeout
        return SamsungTVWS(self._host, **kwargs)

    @staticmethod
    async def _async_run_blocking_contained(fn, timeout: float):
        """Run sync I/O without letting a timed-out worker escape its caller.

        ``asyncio.to_thread`` cannot stop an in-flight socket call. Shielding
        and draining the worker keeps the surrounding Art lock or port attempt
        active until that call has actually returned, so the next operation
        cannot overlap it.
        """
        worker = asyncio.create_task(asyncio.to_thread(fn))
        try:
            return await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            try:
                await worker
            except Exception:  # noqa: BLE001
                pass
            raise

    def _capture_token(self, tv) -> None:
        """Capture a token the TV may have (re)issued on this connection and
        persist it, so authorization stays valid across reconnects instead of
        drifting and re-triggering the approval popup."""
        try:
            new = getattr(tv, "token", None)
        except Exception:  # noqa: BLE001
            new = None
        if new and new != self._token:
            _LOGGER.debug("Token refreshed for %s; persisting new token", self._host)
            self._token = new
            persister = self._token_persister
            if persister:
                try:
                    persister(new)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Token persister failed", exc_info=True)

    def _close_art_connection(self, tv, art=None) -> None:
        """Persist the freshest token and close Art before its parent client."""
        token_source = tv
        if art is not None:
            try:
                if getattr(art, "token", None):
                    token_source = art
            except Exception:  # noqa: BLE001
                pass
        self._capture_token(token_source)

        closed_ids: set[int] = set()
        for client in (art, tv):
            if client is None:
                continue
            client_id = id(client)
            if client_id in closed_ids:
                continue
            closed_ids.add(client_id)
            closer = getattr(client, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    pass

    async def async_send_key(self, key: str) -> None:
        """Send a remote key over a properly-identified connection."""
        def _send():
            tv = self._make_tv()
            try:
                try:
                    tv.remote().send_key(key)
                except Exception:  # noqa: BLE001
                    send_fn = getattr(tv, "send_key", None)
                    if callable(send_fn):
                        send_fn(key)
            finally:
                self._capture_token(tv)
                closer = getattr(tv, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:  # noqa: BLE001
                        pass
        await asyncio.to_thread(_send)

    def _fire_art_changed(self, content_id) -> None:
        """Fire an HA event when the displayed artwork changes (automation hook)."""
        try:
            self.hass.bus.async_fire(
                f"{DOMAIN}_art_changed",
                {"host": self._host, "content_id": content_id},
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to fire art_changed event", exc_info=True)

    async def _async_art(self, fn_name: str, *args):
        """Call a method on the (sync) Art API in an executor thread.

        Brightness / color-temperature / motion / brightness-sensor settings
        live on the synchronous ``SamsungTVArt`` client (``tv.art()``) in the
        official samsungtvws package. We open a short-lived, properly-identified
        connection, invoke the method off the event loop, persist any refreshed
        token, and close. Returns the result, or None on failure.
        """
        try:
            from samsungtvws import SamsungTVWS  # type: ignore  # noqa: F401
        except Exception:  # noqa: BLE001
            return None

        def _call():
            tv = self._make_tv(timeout=ART_OPERATION_TIMEOUT_SECONDS)
            art = None
            try:
                art = tv.art()
                fn = getattr(art, fn_name, None)
                if fn is None:
                    return None
                return fn(*args)
            finally:
                self._close_art_connection(tv, art)

        async with self._art_lock:
            try:
                return await self._async_run_blocking_contained(
                    _call,
                    ART_OPERATION_TIMEOUT_SECONDS,
                )
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("art %s failed: %r", fn_name, e)
                return None

    @staticmethod
    def _coerce_int(val):
        if isinstance(val, dict):
            val = val.get("value")
        try:
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    async def async_get_brightness(self) -> Optional[int]:
        """Return Art Mode brightness (0-10) or None."""
        return self._coerce_int(await self._async_art("get_brightness"))

    async def async_set_brightness(self, value: int) -> None:
        """Set Art Mode brightness (0-10)."""
        await self._async_art("set_brightness", str(int(value)))

    async def async_get_color_temperature(self) -> Optional[int]:
        """Return Art Mode color temperature (-5..5) or None."""
        return self._coerce_int(await self._async_art("get_color_temperature"))

    async def async_set_color_temperature(self, value: int) -> None:
        """Set Art Mode color temperature (-5..5)."""
        await self._async_art("set_color_temperature", str(int(value)))

    async def async_get_state(self) -> dict:
        """Fetch art-mode status AND current content id over a SINGLE connection.

        Used by the media_player coordinator so exposing the current artwork
        adds no extra connections beyond the existing status poll.
        """
        def _read() -> dict:
            tv = self._make_tv(timeout=10)
            art = None
            status = None
            content_id = None
            try:
                art = tv.art()
                try:
                    status = art.get_artmode()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    cur = art.get_current()
                    if isinstance(cur, dict):
                        content_id = cur.get("content_id") or cur.get("contentId")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self._close_art_connection(tv, art)
            return {
                "status": str(status).lower() if status is not None else None,
                "content_id": content_id,
            }

        async with self._art_lock:
            try:
                return await self._async_run_blocking_contained(_read, 10)
            except Exception:  # noqa: BLE001
                return {"status": None, "content_id": None}

    async def async_get_artmode_setting(self, setting: str):
        """Return an art-mode setting value (motion_sensitivity, motion_timer,
        brightness_sensor_setting) or None."""
        res = await self._async_art("get_artmode_settings", setting)
        if isinstance(res, dict):
            return res.get("value", res.get(setting))
        return res

    async def async_set_motion_sensitivity(self, value: int) -> None:
        await self._async_art("set_motion_sensitivity", str(int(value)))

    async def async_set_motion_timer(self, value: str) -> None:
        await self._async_art("set_motion_timer", str(value))

    async def async_set_brightness_sensor(self, enabled: bool) -> None:
        await self._async_art("set_brightness_sensor_setting", "on" if enabled else "off")

    def _get_db(self):
        """Open a sqlite connection to the library DB."""
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def _ensure_db(self) -> None:
        """Ensure the art_library table exists and has necessary columns."""
        if not self._db_path:
            return
            
        def _init_db():
            import sqlite3
            try:
                os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
                with sqlite3.connect(self._db_path) as conn:
                    # Create table if not exists (current schema).
                    # NOTE: keep this column set in sync with every column the
                    # rest of api.py reads/writes (track, rotate, cleanup,
                    # favorites, preview). Older databases are upgraded by the
                    # ALTER migrations below.
                    conn.execute(
                        """
            CREATE TABLE IF NOT EXISTS art_library (
                content_id TEXT PRIMARY KEY,
                created_at TIMESTAMP,
                last_displayed_at TIMESTAMP,
                on_tv INTEGER DEFAULT 0,
                is_favorite INTEGER DEFAULT 0,
                tags TEXT,
                category TEXT,
                source_file TEXT,
                deleted_at TIMESTAMP,
                width INTEGER,
                height INTEGER
            )
        """)

                    # New table for Local Files (AI Tagged)
                    conn.execute("""
            CREATE TABLE IF NOT EXISTS local_art (
                file_path TEXT PRIMARY KEY,
                tags TEXT,
                description TEXT,
                processed_at TIMESTAMP,
                width INTEGER,
                height INTEGER,
                file_size INTEGER
            )
        """)

                    # Migration: bring older art_library tables up to the full
                    # schema. Each ALTER is guarded so it only runs when missing,
                    # which makes this idempotent across versions (including DBs
                    # created by the legacy date_added/last_seen/source schema).
                    existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(art_library)")]
                    _LOGGER.debug("DB Sync: art_library columns: %s", existing_cols)

                    if "created_at" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'created_at' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN created_at TIMESTAMP")
                    if "last_displayed_at" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'last_displayed_at' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN last_displayed_at TIMESTAMP")
                    if "on_tv" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'on_tv' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN on_tv INTEGER DEFAULT 0")
                    if "is_favorite" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'is_favorite' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN is_favorite INTEGER DEFAULT 0")
                    if "tags" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'tags' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN tags TEXT")
                    if "category" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'category' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN category TEXT")
                    if "deleted_at" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'deleted_at' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN deleted_at TIMESTAMP")
                    if "source_file" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'source_file' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN source_file TEXT")
                    if "width" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'width' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN width INTEGER")
                    if "height" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'height' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN height INTEGER")

                    # Migration: local_art
                    local_cols = [row[1] for row in conn.execute("PRAGMA table_info(local_art)")]
                    if "is_favorite" not in local_cols:
                         _LOGGER.info("DB Sync: adding 'is_favorite' column to local_art")
                         conn.execute("ALTER TABLE local_art ADD COLUMN is_favorite INTEGER DEFAULT 0")

                    conn.commit()
            except Exception as e:
                _LOGGER.error("DB Init failed: %s", e)
                raise

        await asyncio.to_thread(_init_db)

    async def async_initialize_database(self) -> None:
        """Create or migrate the library database, raising on failure."""
        await self._ensure_db()



    async def async_track_art(self, content_id: str, tags: Optional[str] = None, source_file: Optional[str] = None) -> None:
        """Track a new upload in the local DB with optional tags and source_file."""
        if not self._db_path or not content_id:
            return
        if source_file:
            source_file = _canonical_source_identity(self.hass, source_file)
        
        await self._ensure_db()

        def _track():
            import sqlite3
            from datetime import datetime
            try:
                with sqlite3.connect(self._db_path) as conn:
                    now_ts = datetime.now().isoformat()
                    # Upsert: if exists, just update last_displayed, on_tv, tags, and source_file
                    conn.execute(
                        """
                        INSERT INTO art_library (content_id, created_at, last_displayed_at, on_tv, tags, source_file)
                        VALUES (?, ?, ?, 1, ?, ?)
                        ON CONFLICT(content_id) DO UPDATE SET
                            last_displayed_at = excluded.last_displayed_at,
                            on_tv = 1,
                            tags = COALESCE(excluded.tags, art_library.tags),
                            source_file = COALESCE(excluded.source_file, art_library.source_file)
                        """,
                        (content_id, now_ts, now_ts, tags, source_file),
                    )
                    conn.commit()
            except Exception as e:
                _LOGGER.error("Failed to track art %s: %s", content_id, e)

        await asyncio.to_thread(_track)

    async def async_toggle_favorite(self, content_id: str) -> bool:
        """Toggle favorite status for an item by content_id or file_path."""
        if not self._db_path or not content_id:
            return False
        
        await self._ensure_db()

        def _toggle():
             import sqlite3
             with sqlite3.connect(self._db_path) as conn:
                 # Check art_library first
                 curr = conn.execute("SELECT is_favorite FROM art_library WHERE content_id=?", (content_id,)).fetchone()
                 if curr:
                     new_val = 1 if not curr[0] else 0
                     conn.execute("UPDATE art_library SET is_favorite=? WHERE content_id=?", (new_val, content_id))
                     conn.commit()
                     return bool(new_val)
                 
                 # Local artwork is addressed only through its opaque media ID.
                 if tracked_path := _local_art_path_for_media_id(conn, content_id):
                     favorite_row = conn.execute(
                         "SELECT is_favorite FROM local_art WHERE file_path=?",
                         (tracked_path,),
                     ).fetchone()
                     if favorite_row:
                         is_favorite = favorite_row[0]
                         new_val = 1 if not is_favorite else 0
                         conn.execute(
                             "UPDATE local_art SET is_favorite=? WHERE file_path=?",
                             (new_val, tracked_path),
                         )
                         conn.commit()
                         return bool(new_val)
                 
                 # If not found, create entry in art_library as favorite (assuming TV ID)
                 # Only if it looks like a TV ID (MY_ or SAM_)
                 if content_id.startswith(("MY_", "SAM-", "SAM_")):
                     import datetime
                     now = datetime.datetime.now().isoformat()
                     conn.execute("INSERT INTO art_library (content_id, is_favorite, created_at, on_tv) VALUES (?, 1, ?, 0)", (content_id, now))
                     conn.commit()
                     return True
                     
                 return False

        return await asyncio.to_thread(_toggle)

    async def async_delete_art(self, media_id: str) -> bool:
        """Delete one tracked local item addressed by its opaque media ID."""
        if not self._db_path or not media_id:
            return False

        await self._ensure_db()

        def _delete():
            import sqlite3

            try:
                with sqlite3.connect(self._db_path) as conn:
                    rows = conn.execute("SELECT file_path FROM local_art").fetchall()
                    if is_local_media_identifier(media_id):
                        tracked_path = _local_art_path_for_media_id(conn, media_id)
                    else:
                        source_row = conn.execute(
                            "SELECT source_file FROM art_library WHERE content_id=?",
                            (media_id,),
                        ).fetchone()
                        tracked_paths = {row[0] for row in rows}
                        tracked_path = (
                            source_row[0]
                            if source_row and source_row[0] in tracked_paths
                            else None
                        )
                    if not tracked_path:
                        return False

                    path = ensure_allowed_local_path(self.hass, tracked_path)
                    if path.exists():
                        try:
                            path.unlink()
                        except OSError as err:
                            _LOGGER.warning(
                                "Failed to delete tracked local artwork for media_id=%s: %s",
                                media_id,
                                err,
                            )
                            return False

                    conn.execute("DELETE FROM local_art WHERE file_path=?", (tracked_path,))
                    conn.execute("DELETE FROM art_library WHERE source_file=?", (tracked_path,))
                    conn.commit()
                    return True
            except UnsafeLocalPathError:
                _LOGGER.warning("Rejected out-of-root deletion for media_id=%s", media_id)
                return False
            except Exception as e:
                _LOGGER.error("Delete failed for media_id=%s: %s", media_id, e)
                return False

        return await asyncio.to_thread(_delete)

    async def async_read_local_art(self, media_id: str) -> dict | None:
        """Read one tracked local artwork by its opaque media identifier."""
        if not self._db_path or not is_local_media_identifier(media_id):
            return None
        await self._ensure_db()

        def _get():
            import sqlite3

            try:
                with sqlite3.connect(self._db_path) as conn:
                    tracked_path = _local_art_path_for_media_id(conn, media_id)
                if tracked_path:
                    path = ensure_allowed_local_path(self.hass, tracked_path)
                    if not path.is_file():
                        return None
                    with path.open("rb") as file_handle:
                        return {
                            "data": file_handle.read(),
                            "path": str(path),
                            "content_type": image_content_type(path),
                        }
            except UnsafeLocalPathError:
                _LOGGER.warning("Rejected out-of-root tracked artwork for media_id=%s", media_id)
            except Exception as e:
                _LOGGER.warning("Local artwork fetch failed for media_id=%s: %s", media_id, e)
            return None

        return await asyncio.to_thread(_get)

    async def async_get_thumbnail(self, media_id: str) -> tuple[bytes, str] | None:
        """Get thumbnail bytes and MIME type for an opaque local media ID."""
        artwork = await self.async_read_local_art(media_id)
        if not artwork:
            return None
        return artwork["data"], artwork["content_type"]

    async def async_get_library_data(self) -> dict:
        """Get all library items for the gallery dashboard."""
        if not self._db_path:
             return {"items": []}
        
        await self._ensure_db()
        
        def _fetch():
            import sqlite3
            items = []
            try:
                with sqlite3.connect(self._db_path) as conn:
                    # FETCH ONLY LOCAL ART (User Request)
                    # Check columns first
                    cols_local = [info[1] for info in conn.execute("PRAGMA table_info(local_art)").fetchall()]
                    has_fav = "is_favorite" in cols_local
                    
                    query = f"SELECT file_path, tags{(', is_favorite' if has_fav else '')} FROM local_art"
                    rows = conn.execute(query).fetchall()
                    
                    for r in rows:
                        try:
                            path = ensure_allowed_local_path(self.hass, r[0])
                        except UnsafeLocalPathError:
                            _LOGGER.warning("Skipping out-of-root tracked artwork")
                            continue
                        if not path.is_file():
                            continue
                        items.append({
                            "id": media_identifier(path),
                            "tags": r[1] or "",
                            "is_favorite": bool(r[2]) if has_fav else False,
                            "type": "local",
                            "name": path.name,
                            "content_type": image_content_type(path),
                        })
            except Exception as e:
                _LOGGER.error("Library fetch failed: %s", e)
            return {"items": items}
        
        return await asyncio.to_thread(_fetch)

    async def async_rotate_art(self, tags: Optional[list[str]] = None, negative_tags: Optional[list[str]] = None, match_all: bool = False, matte: str = "none", source: str = "library") -> bool:
        """Rotate art by selecting from DB (TV or Local), filtering by tags (fuzzy match)."""
        if not self._db_path:
            return False

        await self._ensure_db()

        # 1. Gather Candidates from both tables
        def _get_candidates():
            import sqlite3
            candidates = [] 
            # Format: {'id': str, 'type': 'tv'|'local', 'tags': str, 'path': str|None}
            
            try:
                with sqlite3.connect(self._db_path) as conn:
                    cursor = conn.cursor()
                    
                    # 1a. TV Candidates (Already uploaded)
                    rows_tv = cursor.execute("SELECT content_id, tags FROM art_library WHERE on_tv = 1").fetchall()
                    for r_id, r_tags in rows_tv:
                        candidates.append({'id': r_id, 'type': 'tv', 'tags': r_tags or "", 'path': None})
                        
                    # 1b. Local Candidates (On disk, AI tagged)
                    rows_local = cursor.execute("SELECT file_path, tags FROM local_art").fetchall()
                    for r_path, r_tags in rows_local:
                        candidates.append({'id': None, 'type': 'local', 'tags': r_tags or "", 'path': r_path})
                        
            except Exception as e:
                _LOGGER.error("Rotate: failed to fetch candidates: %s", e)
                return []
            return candidates

        all_candidates = await asyncio.to_thread(_get_candidates)
        
        if not all_candidates:
            _LOGGER.warning("Rotate: No art found in library (TV or Local).")
            return False

        # 2. Filter Candidates (Fuzzy Match)
        filtered = []
        
        # Prepare targets
        targets = [t.strip().lower() for t in tags] if tags else []
        negatives = [t.strip().lower() for t in negative_tags] if negative_tags else []

        # If no specific filters, start with all
        if not targets and not negatives:
            filtered = all_candidates
        else:
            for c in all_candidates:
                c_tags_str = c['tags'].lower()
                
                # A. Check Negatives (Must NOT match)
                excluded = False
                for neg in negatives:
                    if neg in c_tags_str:
                        excluded = True
                        break
                if excluded:
                    continue

                # B. Check Positives (Must match)
                # If no positive tags provided, we match everything that passed exclusion
                if not targets:
                    filtered.append(c)
                    continue

                # Loose matching: check if target substring exists in the tag string
                matches = []
                for target in targets:
                    if target in c_tags_str:
                        matches.append(True)
                    else:
                        matches.append(False)
                
                if match_all:
                    if all(matches): filtered.append(c)
                else:
                    if any(matches): filtered.append(c)
            
        # Replaced "filtered" with new list for favorite filtering
        if source == "favorites":
            # Filter down to only favorites
            fav_filtered = []
            try:
                import sqlite3
                with sqlite3.connect(self._db_path) as conn:
                    cursor = conn.cursor()
                    # Get all favorite content_ids or paths
                    rows_fav = cursor.execute("SELECT content_id FROM art_library WHERE is_favorite=1").fetchall()
                    fav_ids = {r[0] for r in rows_fav}
                    rows_local_fav = cursor.execute("SELECT file_path FROM local_art WHERE is_favorite=1").fetchall()
                    fav_paths = {r[0] for r in rows_local_fav}
                    
                    for f in filtered:
                        if f['type'] == 'tv' and f['id'] in fav_ids:
                            fav_filtered.append(f)
                        elif f['type'] == 'local' and f['path'] in fav_paths:
                            fav_filtered.append(f)
                filtered = fav_filtered
            except Exception as e:
                _LOGGER.warning("Rotate(favorites): error filtering: %s", e)

        if not filtered:
             _LOGGER.info("Rotate: no art matches tags: %s (checked %s items)", tags, len(all_candidates))
             return False

        # 3. Select Winner (with retry for stale local entries)
        import random
        max_attempts = min(10, len(filtered))
        for attempt in range(max_attempts):
            winner = random.choice(filtered)
            _LOGGER.info("Rotate: selected %s (%s)", winner.get('path') or winner.get('id'), winner['type'])

            # 4. Act (Select or Upload+Select)
            try:
                if winner['type'] == 'tv':
                    async with self._art_lock:
                        await self._async_select_image_id(winner['id'], matte=matte)
                    return True
                
                elif winner['type'] == 'local':
                    media_id = await asyncio.to_thread(
                        media_identifier,
                        winner["path"],
                    )
                    artwork = await self.async_read_local_art(
                        media_id
                    )
                    if not artwork:
                        _LOGGER.warning(
                            "Rotate: local item is stale or outside the trusted roots"
                        )
                        filtered.remove(winner)
                        if not filtered:
                            _LOGGER.warning("Rotate: No valid candidates left after removing stale entries")
                            return False
                        continue

                    await self.async_upload_image(
                        artwork["data"],
                        matte=matte,
                        source_file=artwork["path"],
                    )
                    
                    # Note: async_upload_image does not return ID easily in all paths, 
                    # but it DOES select the image after upload.
                    # So we are done!
                    return True
                    
            except Exception as e:
                _LOGGER.error("Rotate: Action failed: %s", e)
                return False
        
        _LOGGER.warning("Rotate: Could not find a valid image after %d attempts", max_attempts)
        return False
        
    async def _async_select_image_id(
        self,
        content_ids: str | list[str],
        matte: str = "none",
        require_available: bool = False,
    ) -> Optional[str]:
        """Select the first matching image ID (best effort)."""
        candidate_ids = (
            [content_ids] if isinstance(content_ids, str) else content_ids
        )

        # Fallback logic similar to upload select
        def _do_select():
            tv = self._make_tv(timeout=30)
            art_client = None
            try:
                art_client = tv.art()
                if require_available:
                    available_ids: set[str] = set()
                    for item in art_client.available() or []:
                        if isinstance(item, dict):
                            available_id = (
                                item.get("id")
                                or item.get("content_id")
                                or item.get("contentId")
                            )
                        else:
                            available_id = str(item)
                        if available_id:
                            available_ids.add(str(available_id))
                    selected_content_id = next(
                        (item for item in candidate_ids if item in available_ids),
                        None,
                    )
                    if selected_content_id is None:
                        return None
                else:
                    selected_content_id = candidate_ids[0] if candidate_ids else None
                    if selected_content_id is None:
                        return None

                # CRITICAL: For change_matte and 3.0.5, "none" is often the literal string expected,
                # but select_image prefers None to clear it.
                tv_matte = matte if matte else "none"
                try:
                    # For select_image, we use None for "none"
                    sel_matte = None if tv_matte == "none" else tv_matte
                    art_client.select_image(
                        selected_content_id,
                        show=True,
                        matte=sel_matte,
                    )
                except TypeError:
                    art_client.select_image(selected_content_id, show=True)
                    # Secondary fallback: use change_matte
                    if hasattr(art_client, "change_matte"):
                        # Do not overwrite portrait_matte_id. LS03D/LS03F
                        # reject that optional parameter for landscape art.
                        final_matte = "none" if tv_matte == "none" else tv_matte
                        art_client.change_matte(
                            selected_content_id,
                            matte_id=final_matte,
                        )
                return selected_content_id
            except Exception as e:
                _LOGGER.debug("Select failed: %s", e)
                if require_available:
                    raise
                return None
            finally:
                self._close_art_connection(tv, art_client)

        worker = asyncio.create_task(asyncio.to_thread(_do_select))
        try:
            selected_content_id = await asyncio.shield(worker)
        except asyncio.CancelledError:
            # A blocking thread cannot be cancelled safely. Keep the caller
            # (and its _art_lock) alive until the socket-bounded worker exits,
            # so no late selection can race a following Art operation.
            try:
                await worker
            except Exception:  # noqa: BLE001
                pass
            raise
        if selected_content_id:
            self._fire_art_changed(selected_content_id)
            return str(selected_content_id)
        return None

    async def async_rotate_from_folder(self, source_dir: str, matte: str = "none") -> bool:
        """Rotate art by picking a random file from a folder and uploading it."""
        if not source_dir:
            return False

        def _pick_and_read():
            import random

            requested = os.path.expanduser(source_dir)
            if not os.path.isabs(requested):
                requested = os.path.join("/media/frame/library", requested)
            try:
                path = ensure_allowed_local_path(self.hass, requested)
            except UnsafeLocalPathError:
                _LOGGER.error("Rotate(folder): source is outside the trusted roots")
                return None, None

            if not path.is_dir():
                _LOGGER.warning("Rotate(folder): path %s does not exist", path)
                return None, None

            exts = {".jpg", ".jpeg", ".png", ".webp"}
            files = []
            try:
                with os.scandir(str(path)) as it:
                    for entry in it:
                        if not entry.is_file() or os.path.splitext(entry.name)[1].lower() not in exts:
                            continue
                        try:
                            files.append(ensure_allowed_local_path(self.hass, entry.path))
                        except UnsafeLocalPathError:
                            _LOGGER.warning("Rotate(folder): skipping out-of-root file")
            except Exception as e:
                _LOGGER.warning("Rotate(folder): error scanning %s: %s", path, e)
                return None, None

            if not files:
                 _LOGGER.warning("Rotate(folder): no images in %s", path)
                 return None, None
            
            file_path = random.choice(files)
            try:
                with file_path.open("rb") as file_handle:
                    return str(file_path), file_handle.read()
            except Exception as e:
                _LOGGER.warning("Rotate(folder): read error %s: %s", file_path, e)
                return None, None

        file_path, image_bytes = await asyncio.to_thread(_pick_and_read)
        if not file_path or not image_bytes:
            return False

        try:
            _LOGGER.debug("Rotate(folder): uploading %s", file_path)
            await self.async_upload_image(image_bytes, matte=matte, source_file=file_path)
            return True
        except Exception as e:
            _LOGGER.error("Rotate(folder): failed to set art from %s: %s", file_path, e)
            return False



    @property
    def is_connected(self) -> bool:
        """Return True if connected to the TV."""
        return self._connected

    @property
    def host(self) -> str:
        return self._host

    @property
    def token(self) -> Optional[str]:
        return self._token

    @property
    def duid(self) -> Optional[str]:
        return self._duid

    async def async_connect_and_pair(self) -> None:
        """Validate the persisted TV identity without opening a pairing flow."""
        _LOGGER.debug(
            "Client: startup validation host=%s token_present=%s",
            self._host,
            bool(self._token),
        )
        self._connected = False
        self._duid = None

        if not self._token:
            raise AuthenticationRejectedError(
                f"No persisted authentication token for {self._host}"
            )

        def _validate(port: int) -> dict:
            tv = None
            art = None
            try:
                tv = self._make_tv(
                    port=port,
                    timeout=CONNECTION_ATTEMPT_TIMEOUT_SECONDS,
                )
                art = tv.art()
                # Opening the Art websocket proves the saved token is accepted.
                # ``art.supported()`` and device info are both public REST and
                # therefore cannot be used as authentication evidence.
                art.open()
                info = tv.rest_device_info()
                device = info.get("device") if isinstance(info, dict) else None
                if not isinstance(device, dict) or not device:
                    raise DeviceUnavailableError(
                        f"Device information unavailable for {self._host}"
                    )
                return info
            finally:
                if tv is not None:
                    self._close_art_connection(tv, art)

        ports = [self._port] if self._port is not None else [8002, 8001]
        last_error: Exception | None = None
        for port in ports:
            try:
                info = await self._async_run_blocking_contained(
                    lambda selected_port=port: _validate(selected_port),
                    CONNECTION_ATTEMPT_TIMEOUT_SECONDS,
                )
            except Exception as err:  # noqa: BLE001
                if type(err).__name__ == "UnauthorizedError":
                    raise AuthenticationRejectedError(
                        f"Stored authentication was rejected by {self._host}"
                    ) from err
                last_error = err
                continue

            device = info["device"]
            self._duid = device.get("duid") or device.get("udn")
            self._port = port
            self._connected = True
            token_path = self._token_file_path
            if token_path:
                def _remove_pairing_file() -> None:
                    try:
                        os.remove(token_path)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        _LOGGER.debug(
                            "Could not remove obsolete pairing token file for host=%s",
                            self._host,
                        )

                await asyncio.to_thread(_remove_pairing_file)
            _LOGGER.info(
                "Client: authenticated host=%s port=%s duid=%s",
                self._host,
                port,
                self._duid,
            )
            return

        raise DeviceUnavailableError(
            f"Unable to validate saved authentication for {self._host}"
        ) from last_error

    async def async_disconnect(self) -> None:
        if self._connected:
            _LOGGER.debug("Disconnecting from Samsung Frame")
            await asyncio.sleep(0.05)
            self._connected = False

    async def async_get_artmode_status(self) -> Optional[str]:
        """Return current Art Mode status as 'on'/'off'/None with best effort logging."""
        async with self._art_lock:
            return await self._async_get_artmode_status_locked()

    async def _async_get_artmode_status_locked(self) -> Optional[str]:
        """Read Art Mode status while the caller owns ``_art_lock``."""
        try:
            from samsungtvws import SamsungTVWS  # type: ignore  # noqa: F401
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("get_artmode: samsungtvws unavailable: %r", err)
            return None

        def _read_status() -> Optional[str]:
            tv = None
            art = None
            try:
                tv = self._make_tv(timeout=10)
            except (ConnectionError, TimeoutError, OSError) as e:
                _LOGGER.debug("get_artmode: connection error on %s: %r", self._host, e)
                return None
            try:
                art = tv.art()
                status = art.get_artmode()
                return str(status).lower() if status is not None else None
            except (ConnectionError, TimeoutError, ValueError, OSError) as e:
                _LOGGER.debug("get_artmode: request error on %s: %r", self._host, e)
                return None
            finally:
                if tv is not None:
                    self._close_art_connection(tv, art)

        try:
            status = await self._async_run_blocking_contained(_read_status, 10)
        except Exception:
            status = None
        _LOGGER.debug("get_artmode: status=%s on %s", status, self._host)
        return status

    async def async_get_current_art(self) -> dict:
        """Fetch info and thumbnail of currently displayed artwork."""
        # Simple caching to avoid over-polling and connection timeouts
        now = __import__("time").time()
        if hasattr(self, "_art_preview_cache") and (now - self._art_preview_cache_time < 5):
            return self._art_preview_cache

        results = {"content_id": None, "image": None}
        try:
            from samsungtvws import SamsungTVWS  # noqa: F401
        except Exception:
            return results

        def _fetch():
            tv = None
            art_client = None
            try:
                tv = self._make_tv(timeout=ART_OPERATION_TIMEOUT_SECONDS)
                art_client = tv.art()
                # Prime the art channel
                try:
                    art_client.supported()
                except Exception:
                    pass

                # Select the correct thumbnail method based on discovery
                # Discovery showed 'get_thumbnail' is the correct one for this model/version
                
                curr = art_client.get_current()
                if curr:
                    results["content_id"] = curr.get("content_id") or curr.get("contentId")
                    _LOGGER.debug("Art Preview: current content_id is %s", results["content_id"])
                    
                    # NEW: Lookup local file path first for high-res instant preview
                    if results["content_id"] and self._db_path:
                        try:
                            import sqlite3
                            with sqlite3.connect(self._db_path) as conn:
                                row = conn.execute(
                                    """
                                    SELECT art_library.source_file
                                    FROM art_library
                                    JOIN local_art
                                      ON local_art.file_path = art_library.source_file
                                    WHERE art_library.content_id = ?
                                    """,
                                    (results["content_id"],),
                                ).fetchone()
                                local_path = (
                                    ensure_allowed_local_path(self.hass, row[0])
                                    if row and row[0]
                                    else None
                                )
                                if local_path and local_path.is_file():
                                    _LOGGER.debug("Art Preview: using local file lookup for %s", results["content_id"])
                                    with local_path.open("rb") as file_handle:
                                        results["image"] = file_handle.read()
                                    if results["image"]:
                                        return
                        except Exception as e:
                            if "no such column: source_file" in str(e):
                                _LOGGER.debug("Art Preview: source_file column missing, falling back to TV download")
                            else:
                                _LOGGER.debug("Art Preview: local lookup failed for %s: %r", results["content_id"], e)

                    # 1. Try get_thumbnail
                    get_thumbnail_fn = getattr(art_client, "get_thumbnail", None)
                    if get_thumbnail_fn:
                        try:
                            _LOGGER.debug("Art Preview: calling get_thumbnail for %s", results["content_id"])
                            results["image"] = get_thumbnail_fn(results["content_id"])
                            if results["image"]:
                                _LOGGER.debug("Art Preview: get_thumbnail success for %s (%d bytes)", results["content_id"], len(results["image"]))
                                return
                            _LOGGER.debug("Art Preview: get_thumbnail returned empty for %s", results["content_id"])
                        except Exception as e:
                            _LOGGER.debug("Art Preview: get_thumbnail failed for %s: %r", results["content_id"], e)

                    # 2. Try get_preview as fallback
                    get_preview_fn = getattr(art_client, "get_preview", None)
                    if get_preview_fn:
                        try:
                            _LOGGER.debug("Art Preview: calling get_preview for %s", results["content_id"])
                            results["image"] = get_preview_fn(results["content_id"])
                            if results["image"]:
                                _LOGGER.debug("Art Preview: get_preview success for %s (%d bytes)", results["content_id"], len(results["image"]))
                                return
                            _LOGGER.debug("Art Preview: get_preview returned empty for %s", results["content_id"])
                        except Exception as e:
                            _LOGGER.debug("Art Preview: get_preview failed for %s: %r", results["content_id"], e)

                    # 3. Try get_photo as final fallback
                    get_photo_fn = getattr(art_client, "get_photo", None)
                    if get_photo_fn:
                        try:
                            _LOGGER.debug("Art Preview: calling get_photo for %s", results["content_id"])
                            results["image"] = get_photo_fn(results["content_id"])
                            if results["image"]:
                                _LOGGER.debug("Art Preview: get_photo success for %s (%d bytes)", results["content_id"], len(results["image"]))
                                return
                            _LOGGER.debug("Art Preview: get_photo returned empty for %s", results["content_id"])
                        except Exception as e:
                            _LOGGER.debug("Art Preview: get_photo failed for %s: %r", results["content_id"], e)
                else:
                    _LOGGER.debug("Art Preview: get_current returned None/Empty")

            except Exception as e:
                _LOGGER.debug("Error fetching current art: %r", e)
            finally:
                if tv:
                    self._close_art_connection(tv, art_client)

        async with self._art_lock:
            try:
                await self._async_run_blocking_contained(
                    _fetch,
                    ART_OPERATION_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                _LOGGER.debug("Art Preview: fetch thread timed out after 15s")
            except Exception as e:
                _LOGGER.debug("Art Preview: fetch thread error: %r", e)
            
        self._art_preview_cache = results
        self._art_preview_cache_time = now
        return results

    async def async_set_artmode(self, enabled: bool) -> None:
        """Enable or disable Art Mode using samsungtvws."""
        async with self._art_lock:
            await self._async_set_artmode_locked(enabled)

    async def _async_set_artmode_locked(self, enabled: bool) -> None:
        """Internal set_artmode assuming caller holds _art_lock."""
        # Early exit if already in desired state to avoid unnecessary requests
        try:
            current = await self._async_get_artmode_status_locked()
            if current is not None:
                if bool(enabled) and current in ("on", "true", "1"):
                    _LOGGER.debug("ArtMode: already on for %s; skipping", self._host)
                    return
                if not bool(enabled) and current in ("off", "false", "0", "none"):
                    _LOGGER.debug("ArtMode: already off for %s; skipping", self._host)
                    return
        except Exception:
            pass
        # Sync client in executor (official samsungtvws art() API).
        try:
            from samsungtvws import SamsungTVWS  # type: ignore  # noqa: F401
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("samsungtvws import failed, cannot set art mode: %s", err)
            await asyncio.sleep(0.01)
            return

        def _make_client():
            _LOGGER.debug(
                "Creating identified client for host=%s token_present=%s",
                self._host,
                bool(self._token),
            )
            return self._make_tv(
                port=self._port,
                timeout=ART_OPERATION_TIMEOUT_SECONDS,
            )

        def _set():
            tv_local = _make_client()
            art_client = None
            _LOGGER.debug("ArtMode: sending set_artmode(%s) to %s", bool(enabled), self._host)
            import time
            last_status = None
            # Precompute selection candidate once to reduce available() calls
            selection_candidate = None
            try:
                art_client = tv_local.art()
                try:
                    current = art_client.get_current()
                except Exception:
                    current = None
                if isinstance(current, dict):
                    selection_candidate = current.get("content_id") or current.get("contentId")
                if not selection_candidate:
                    try:
                        avail = art_client.available() or []
                    except Exception:
                        avail = []
                    for item in avail:
                        image_id = None
                        if isinstance(item, dict):
                            image_id = item.get("id") or item.get("content_id") or item.get("contentId")
                        elif isinstance(item, str):
                            image_id = item
                        if not image_id:
                            continue
                        normalized = str(image_id)
                        normalized_dash = normalized.replace("_", "-")
                        if normalized_dash.upper().startswith("MY-"):
                            selection_candidate = image_id
                            break
                        if not selection_candidate and normalized_dash.upper().startswith("SAM-"):
                            selection_candidate = image_id
                try:
                    art_client.set_artmode(bool(enabled))
                except Exception as exc:  # noqa: BLE001
                    # Note: This often fires due to the TV's clientConnect handshake event
                    # being misinterpreted as a failure. Art Mode usually still activates.
                    _LOGGER.debug("ArtMode: set_artmode(%s) initial call on %s: %r", bool(enabled), self._host, exc)

                # Verification + fallback loop (up to ~10s)
                for attempt in range(1, 3 + 1):
                    try:
                        status = art_client.get_artmode()
                        last_status = status
                        _LOGGER.debug("ArtMode: attempt %s status=%s on %s", attempt, status, self._host)
                        if bool(enabled) and str(status).lower() in ("on", "true", "1"):
                            break
                        if not bool(enabled) and str(status).lower() in ("off", "false", "0", "none"):
                            break
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("ArtMode: verification not available at attempt %s on %s", attempt, self._host)
                    # On enable, force-select an image to coax Art Mode ON (even if current exists)
                    if bool(enabled) and attempt in (1, 3):
                        try:
                            if selection_candidate:
                                _LOGGER.debug("ArtMode: selecting image %s on %s to force Art Mode on", selection_candidate, self._host)
                                art_client.select_image(selection_candidate, show=True)
                        except Exception as sel_err:
                            _LOGGER.debug("ArtMode: select image fallback failed on %s: %r", self._host, sel_err)
                    time.sleep(2)
                _LOGGER.debug("ArtMode: final status=%s on %s", last_status, self._host)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._close_art_connection(tv_local, art_client)

        await self._async_run_blocking_contained(
            _set,
            ART_OPERATION_TIMEOUT_SECONDS,
        )

    async def async_preprocess_image(self, image_bytes: bytes) -> bytes:
        """Resize to 3840x2160 and return JPEG bytes.

        Mode 'crop' scales to fill then center-crops (may trim edges); mode
        'fit' scales to fit and pads with black (letterbox), preserving the
        whole image — better for portraits/posters.
        """
        try:
            from PIL import Image
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Pillow not available: %s", err)
            raise

        mode = self._resize_mode

        def _process() -> bytes:
            with Image.open(io.BytesIO(image_bytes)) as im:  # type: ignore
                im_converted = im.convert("RGB")

                target_w, target_h = 3840, 2160
                src_w, src_h = im_converted.width, im_converted.height
                src_ratio = src_w / src_h
                tgt_ratio = target_w / target_h

                if mode == "fit":
                    # Scale to fit entirely, then pad to target (letterbox).
                    if src_ratio > tgt_ratio:
                        new_w = target_w
                        new_h = max(1, round(target_w / src_ratio))
                    else:
                        new_h = target_h
                        new_w = max(1, round(target_h * src_ratio))
                    resized = im_converted.resize((new_w, new_h), Image.LANCZOS)
                    canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
                    canvas.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
                    result = canvas
                else:
                    # Scale to fill, then center-crop.
                    if src_ratio > tgt_ratio:
                        scale = target_h / src_h
                        new_w = int(src_w * scale)
                        new_h = target_h
                    else:
                        scale = target_w / src_w
                        new_w = target_w
                        new_h = int(src_h * scale)
                    resized = im_converted.resize((new_w, new_h), Image.LANCZOS)
                    left = (new_w - target_w) // 2
                    top = (new_h - target_h) // 2
                    result = resized.crop((left, top, left + target_w, top + target_h))

                out = io.BytesIO()
                result.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
                return out.getvalue()

        import io

        return await asyncio.to_thread(_process)

    async def async_upload_image(
        self,
        image_bytes: bytes,
        matte: str = "none",
        source_file: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> Optional[str]:
        """Upload an image, select it, and return the TV content ID."""
        if source_file:
            source_file = _canonical_source_identity(self.hass, source_file)

        async def _reuse_existing_upload() -> Optional[str]:
            if not source_file or not self._db_path:
                return None

            await self._ensure_db()

            def _find_existing_content_ids() -> list[str]:
                import sqlite3

                try:
                    with sqlite3.connect(self._db_path) as conn:
                        rows = conn.execute(
                            """
                            SELECT content_id
                            FROM art_library
                            WHERE source_file = ?
                            ORDER BY COALESCE(last_displayed_at, created_at) DESC
                            """,
                            (source_file,),
                        ).fetchall()
                    return [str(row[0]) for row in rows]
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning(
                        "Upload: existing source lookup failed for %s: %r",
                        source_file,
                        err,
                    )
                    raise

            existing_content_ids = await asyncio.to_thread(
                _find_existing_content_ids
            )
            if not existing_content_ids:
                return None

            selected_content_id = await self._async_select_image_id(
                existing_content_ids,
                matte=matte,
                require_available=True,
            )
            if selected_content_id:
                await self.async_track_art(
                    selected_content_id,
                    tags=tags,
                    source_file=source_file,
                )
                _LOGGER.info(
                    "Upload: reused existing content_id=%s for source=%s on host=%s",
                    selected_content_id,
                    source_file,
                    self._host,
                )
                return selected_content_id
            _LOGGER.debug(
                "Upload: tracked content IDs for source=%s are absent from "
                "target host=%s; uploading",
                source_file,
                self._host,
            )
            return None

        if source_file and self._db_path:
            async with self._art_lock:
                existing_content_id = await _reuse_existing_upload()
            if existing_content_id:
                return existing_content_id

        processed = await self.async_preprocess_image(image_bytes)
        _LOGGER.debug("Upload: processed image size=%s bytes for host=%s", len(processed), self._host)

        # Optional preflight removed to reduce chatter; rely on upload errors for feedback

        try:
            from samsungtvws import SamsungTVWS  # type: ignore
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("samsungtvws import failed, cannot upload image: %s", err)
            return

        def _make_client(timeout: float):
            # Force SSL websocket port 8002 for upload operations
            return self._make_tv(port=8002, timeout=timeout)

        def _upload_once():
            tv = _make_client(120)
            art_client = None
            try:
                _LOGGER.debug("Upload: starting art.upload on %s (matte=%s)", self._host, matte)
                # Pass matte to upload so it applies immediately if supported
                art_client = tv.art()
                remote_filename = art_client.upload(processed, file_type="JPEG", matte=matte)
                _LOGGER.debug("Upload: art.upload returned filename=%s on %s", remote_filename, self._host)
                return remote_filename
            finally:
                self._close_art_connection(tv, art_client)

        def _select_once(remote_filename: str) -> None:
            """Select an uploaded image without ever repeating the upload."""
            tv = _make_client(30)
            art_client = None
            try:
                # For change_matte and 3.0.5, "none" is the literal string
                # expected, while select_image prefers None to clear it.
                tv_matte = matte if matte else "none"
                art_client = tv.art()
                try:
                    sel_matte = None if tv_matte == "none" else tv_matte
                    art_client.select_image(remote_filename, show=True, matte=sel_matte)
                    _LOGGER.debug("Upload: select_image success on %s (matte=%s)", self._host, tv_matte)
                except TypeError:
                    # Fallback for older library versions without the matte keyword.
                    _LOGGER.debug("Upload: select_image does not support 'matte' keyword, falling back")
                    art_client.select_image(remote_filename, show=True)
                    if hasattr(art_client, "change_matte"):
                        final_matte = "none" if tv_matte == "none" else tv_matte
                        art_client.change_matte(
                            remote_filename,
                            matte_id=final_matte,
                        )
            finally:
                self._close_art_connection(tv, art_client)

        async with self._art_lock:
            # Another caller may have uploaded this source while preprocessing.
            existing_content_id = await _reuse_existing_upload()
            if existing_content_id:
                return existing_content_id

            # Retry a few times on transient art channel ConnectionFailure
            backoff_seconds = [0.75, 1.5, 2.5, 4.0]
            for attempt in range(1, 5 + 1):
                try:
                    # Prime art channel just before attempt
                    try:
                        from samsungtvws import SamsungTVWS  # type: ignore  # noqa: F401
                        def _prime():
                            tvp = self._make_tv(
                                timeout=ART_OPERATION_TIMEOUT_SECONDS
                            )
                            art_client = None
                            try:
                                art_client = tvp.art()
                                try:
                                    art_client.supported()
                                except Exception:
                                    pass
                                try:
                                    art_client.get_artmode()
                                except Exception:
                                    pass
                            finally:
                                self._close_art_connection(tvp, art_client)
                        await self._async_run_blocking_contained(
                            _prime,
                            ART_OPERATION_TIMEOUT_SECONDS,
                        )
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass

                    res = await self._async_run_blocking_contained(
                        _upload_once,
                        120,
                    )
                    _LOGGER.info("Upload success on host=%s (attempt %s, content_id=%s)", self._host, attempt, res)
                    if res:
                        await self.async_track_art(
                            res,
                            tags=tags,
                            source_file=source_file,
                        )
                        try:
                            await self._async_run_blocking_contained(
                                lambda: _select_once(str(res)),
                                30,
                            )
                        except asyncio.TimeoutError:
                            _LOGGER.warning(
                                "Upload selection timed out on host=%s for content_id=%s; "
                                "not repeating the completed upload",
                                self._host,
                                res,
                            )
                        except Exception as err:  # noqa: BLE001
                            _LOGGER.warning(
                                "Upload selection failed on host=%s for content_id=%s: %r; "
                                "not repeating the completed upload",
                                self._host,
                                res,
                                err,
                            )
                        self._fire_art_changed(res)
                    try:
                        # Confirm selection by logging current content id
                        diag_ok = await self._async_art_diagnostics_locked(
                            max_ids=1
                        )
                        _LOGGER.debug("Upload post-check on %s: current=%s", self._host, diag_ok.get("current"))
                    except Exception:
                        pass
                    if res:
                        return str(res)
                    break
                except asyncio.TimeoutError:
                    # asyncio.to_thread cannot cancel the synchronous upload.
                    # Retrying could therefore create a duplicate if the TV
                    # accepted the first request before the local timeout.
                    _LOGGER.warning(
                        "Upload timed out on host=%s (attempt %s); outcome unknown, "
                        "not retrying to avoid a duplicate",
                        self._host,
                        attempt,
                    )
                    raise
                except Exception as exc:  # noqa: BLE001
                    # Detect samsungtvws ConnectionFailure without importing globally
                    exc_name = type(exc).__name__
                    if exc_name == "ConnectionFailure" and attempt < 5:
                        _LOGGER.debug("Upload ConnectionFailure on %s, retrying (attempt %s)", self._host, attempt)
                        await asyncio.sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)])
                        continue
                    raise
        return None

    async def async_art_diagnostics(self, max_ids: int = 10) -> dict:
        """Collect Art Mode diagnostics via samsungtvws.

        Returns dict with supported, status, current id, available sample ids.
        """
        async with self._art_lock:
            return await self._async_art_diagnostics_locked(max_ids)

    async def _async_art_diagnostics_locked(self, max_ids: int = 10) -> dict:
        """Collect diagnostics while the caller owns ``_art_lock``."""
        try:
            from samsungtvws import SamsungTVWS  # type: ignore  # noqa: F401
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Diagnostics: samsungtvws unavailable: %s", err)
            return {"error": str(err)}

        def _collect() -> dict:
            tv = self._make_tv(timeout=ART_OPERATION_TIMEOUT_SECONDS)
            art = None
            result: dict = {"host": self._host}
            try:
                try:
                    art = tv.art()
                except Exception as e:  # noqa: BLE001
                    result["connection_error"] = repr(e)
                    return result
                try:
                    result["supported"] = art.supported()
                except Exception as e:  # noqa: BLE001
                    result["supported_error"] = repr(e)
                try:
                    status = art.get_artmode()
                    result["status"] = status
                except Exception as e:  # noqa: BLE001
                    result["status_error"] = repr(e)
                try:
                    current = art.get_current()
                    result["current"] = current
                except Exception as e:  # noqa: BLE001
                    result["current_error"] = repr(e)
                try:
                    avail = art.available() or []
                    ids: list[str] = []
                    for item in avail:
                        image_id = None
                        if isinstance(item, dict):
                            image_id = item.get("id") or item.get("content_id") or item.get("contentId")
                        elif isinstance(item, str):
                            image_id = item
                        if image_id:
                            ids.append(image_id)
                        if len(ids) >= max_ids:
                            break
                    result["available_ids"] = ids
                except Exception as e:  # noqa: BLE001
                    result["available_error"] = repr(e)
                return result
            finally:
                self._close_art_connection(tv, art)

        data = await self._async_run_blocking_contained(
            _collect,
            ART_OPERATION_TIMEOUT_SECONDS,
        )
        _LOGGER.info("Diagnostics(Art): %s", data)
        return data

    async def async_cleanup_storage(self, max_items=50, only_integration_managed=True, max_age_days=None, preserve_current=True, dry_run=False):
        """Perform storage cleanup."""
        # Read TV state and available list
        try:
            from samsungtvws import SamsungTVWS  # type: ignore
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Cleanup: samsungtvws unavailable: %s", err)
            return {"error": str(err)}

        def _fetch_tv_state():
            tv = self._make_tv(timeout=ART_OPERATION_TIMEOUT_SECONDS)
            art = None
            current_id = None
            available: list = []
            try:
                art = tv.art()
                cur = art.get_current()
                if isinstance(cur, dict):
                    current_id = cur.get("content_id") or cur.get("contentId")
            except Exception:
                current_id = None
            try:
                if art is not None:
                    available = art.available() or []
            except Exception:
                available = []
            finally:
                self._close_art_connection(tv, art)
            normalized_ids: list[str] = []
            for item in available:
                if isinstance(item, dict):
                    cid = item.get("id") or item.get("content_id") or item.get("contentId")
                else:
                    cid = str(item)
                if cid:
                    normalized_ids.append(str(cid))
            # Deduplicate to prevent double-counting or errors
            return current_id, list(dict.fromkeys(normalized_ids))

        async with self._art_lock:
            current_id, on_tv_ids = await self._async_run_blocking_contained(
                _fetch_tv_state,
                ART_OPERATION_TIMEOUT_SECONDS,
            )

        if preserve_current and not current_id:
            return {
                "current": None,
                "on_tv": len(on_tv_ids),
                "candidates": 0,
                "to_delete": [],
                "deleted": [],
                "skipped_current": [],
                "skipped_favorites": [],
                "errors": [
                    "Current artwork could not be determined; deletion aborted"
                ],
                "dry_run": bool(dry_run),
            }

        # Destructive cleanup is always provenance-gated. DB sync also records
        # manually uploaded TV art, so mere DB presence is not proof that this
        # integration owns an item. Only a non-empty source_file marks an image
        # uploaded by this integration. If provenance cannot be read, fail
        # closed and delete nothing.
        candidates: list[str] = list(on_tv_ids)
        skipped_favorites: list[str] = []
        db_rows: dict[str, dict] = {}

        if not only_integration_managed:
            _LOGGER.debug(
                "Cleanup: ignoring only_integration_managed=False; "
                "manual TV art is never deletion-eligible"
            )

        if self._db_path:
            import sqlite3
            def _db_fetch(ids: list[str]) -> dict[str, dict]:
                if not ids:
                    return {}
                placeholders = ",".join(["?"] * len(ids))
                q = (
                    "SELECT content_id, is_favorite, created_at, "
                    "last_displayed_at, on_tv, source_file FROM art_library "
                    f"WHERE content_id IN ({placeholders}) "
                    "AND source_file IS NOT NULL AND TRIM(source_file) != ''"
                )
                out: dict[str, dict] = {}
                try:
                    conn = sqlite3.connect(self._db_path)
                    try:
                        cur = conn.cursor()
                        for row in cur.execute(q, ids):
                            out[str(row[0])] = {
                                "is_favorite": bool(row[1]),
                                "created_at": row[2],
                                "last_displayed_at": row[3],
                                "on_tv": bool(row[4]),
                                "source_file": row[5],
                            }
                    finally:
                        conn.close()
                except Exception as e:  # noqa: BLE001
                    _LOGGER.debug("Cleanup: DB fetch failed: %r", e)
                return out
            db_rows = await asyncio.to_thread(_db_fetch, candidates)

        candidates = [cid for cid in candidates if cid in db_rows]

        # Filter favorites and optionally current
        if db_rows:
            for cid, meta in db_rows.items():
                if meta.get("is_favorite"):
                    skipped_favorites.append(cid)
        to_consider = [cid for cid in candidates if cid not in skipped_favorites]
        if preserve_current and current_id:
            to_consider = [cid for cid in to_consider if cid != current_id]

        # Apply age filter if available and DB has timestamps
        if max_age_days is not None and db_rows:
            import datetime as dt
            def _older_than_days(cid: str) -> bool:
                created = db_rows.get(cid, {}).get("created_at")
                if not created:
                    return False
                try:
                    # Expect ISO8601 string
                    created_dt = dt.datetime.fromisoformat(str(created))
                    return (dt.datetime.now(created_dt.tzinfo) - created_dt).days >= int(max_age_days)
                except Exception:
                    return False
            aged = [cid for cid in to_consider if _older_than_days(cid)]
        else:
            aged = list(to_consider)

        # Apply max_items: keep the most recently displayed/created
        ordered = list(aged)
        if max_items is not None and db_rows:
            def _sort_key(cid: str):
                meta = db_rows.get(cid, {})
                return meta.get("last_displayed_at") or meta.get("created_at") or ""
            ordered = sorted(aged, key=_sort_key)  # oldest first
            # Determine how many to delete to reach the limit
            excess = max(0, len(to_consider) - int(max_items))
            if excess > 0:
                ordered = ordered[:excess]
            else:
                ordered = []

        to_delete = ordered
        # Dedupe while preserving order
        seen = set()
        deduped: list[str] = []
        for cid in to_delete:
            if cid in seen:
                continue
            seen.add(cid)
            deduped.append(cid)
        to_delete = deduped
        summary = {
            "current": current_id,
            "on_tv": len(on_tv_ids),
            "candidates": len(candidates),
            "to_delete": to_delete,
            "deleted": [],
            "skipped_current": [current_id] if preserve_current and current_id else [],
            "skipped_favorites": skipped_favorites,
            "errors": [],
            "dry_run": bool(dry_run),
        }

        if dry_run or not to_delete:
            _LOGGER.info("Cleanup(dry_run=%s): would delete %s ids on %s (sample=%s)", dry_run, len(to_delete), self._host, to_delete[:10])
            return summary

        # Execute deletion in batches under art lock
        async with self._art_lock:
            if preserve_current:
                def _read_current_id() -> str | None:
                    tv = self._make_tv(
                        timeout=ART_OPERATION_TIMEOUT_SECONDS
                    )
                    art = None
                    try:
                        art = tv.art()
                        current = art.get_current()
                        if isinstance(current, dict):
                            return current.get("content_id") or current.get(
                                "contentId"
                            )
                        return None
                    finally:
                        self._close_art_connection(tv, art)

                try:
                    latest_current = await self._async_run_blocking_contained(
                        _read_current_id,
                        ART_OPERATION_TIMEOUT_SECONDS,
                    )
                except Exception:  # noqa: BLE001
                    summary["errors"].append(
                        "Current artwork could not be revalidated; deletion aborted"
                    )
                    summary["to_delete"] = []
                    return summary
                if latest_current in to_delete:
                    to_delete = [
                        content_id
                        for content_id in to_delete
                        if content_id != latest_current
                    ]
                    summary["to_delete"] = to_delete
                    if latest_current not in summary["skipped_current"]:
                        summary["skipped_current"].append(latest_current)
                if not to_delete:
                    return summary

            try:
                from samsungtvws import SamsungTVWS  # type: ignore  # noqa: F401
            except Exception as err:  # noqa: BLE001
                summary["errors"].append(str(err))
                return summary

            def _delete(ids: list[str]) -> tuple[list[str], list[str]]:
                deleted: list[str] = []
                errors: list[str] = []
                if not ids:
                    return deleted, errors
                tv = self._make_tv(timeout=ART_OPERATION_TIMEOUT_SECONDS)
                art = None
                try:
                    art = tv.art()
                    batch = list(ids)
                    # Prefer delete_list if present
                    try:
                        art.delete_list(batch)
                        deleted = batch
                    except Exception:
                        # Fallback: delete one by one
                        for cid in batch:
                            try:
                                art.delete(cid)
                                deleted.append(cid)
                            except Exception as e:  # noqa: BLE001
                                errors.append(f"{cid}: {e!r}")
                finally:
                    self._close_art_connection(tv, art)
                return deleted, errors

            deleted, errs = await self._async_run_blocking_contained(
                lambda: _delete(to_delete),
                ART_OPERATION_TIMEOUT_SECONDS,
            )
            summary["deleted"] = deleted
            summary["errors"] = errs

        # 5. Update DB flags if we have a DB
        if self._db_path:
            import sqlite3
            def _sync_db_with_tv(deleted_ids: list[str], current_ids: list[str]) -> None:
                try:
                    conn = sqlite3.connect(self._db_path)
                    try:
                        cur = conn.cursor()
                        now_iso = __import__("datetime").datetime.now().isoformat()
                        
                        # Mark specifically deleted items
                        for cid in deleted_ids:
                            cur.execute(
                                "UPDATE art_library SET on_tv=0, deleted_at=? WHERE content_id=?",
                                (now_iso, cid),
                            )
                        
                        # Prune ANY item in our DB that is no longer on the TV hardware
                        # and wasn't just marked as deleted.
                        if current_ids:
                            placeholders = ",".join(["?"] * len(current_ids))
                            cur.execute(
                                f"UPDATE art_library SET on_tv=0 WHERE on_tv=1 AND content_id NOT IN ({placeholders})",
                                current_ids
                            )
                        
                        conn.commit()
                    finally:
                        conn.close()
                except Exception as e:  # noqa: BLE001
                    _LOGGER.debug("Cleanup: DB sync failed: %r", e)
            
            await asyncio.to_thread(_sync_db_with_tv, summary["deleted"], list(on_tv_ids))

        _LOGGER.info(
            "Cleanup(done): deleted=%s skipped_current=%s skipped_favorites=%s errors=%s on %s",
            len(summary["deleted"]), len(summary["skipped_current"]), len(summary["skipped_favorites"]), len(summary["errors"]), self._host,
        )
        return summary

    async def async_add_local_art(self, file_path, tags, description, width, height, file_size):
        """Add -local only- art to the database."""
        await self._ensure_db()

        def _add():
            with self._get_db() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO local_art 
                    (file_path, tags, description, processed_at, width, height, file_size)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (file_path, tags, description, __import__("datetime").datetime.now().isoformat(), width, height, file_size)
                )
        await self.hass.async_add_executor_job(_add)

    async def async_get_local_art_paths(self) -> list[str]:
        """Return a list of all file paths currently in the local_art database."""
        await self._ensure_db()
        def _get():
            try:
                with self._get_db() as conn:
                    rows = conn.execute("SELECT file_path FROM local_art").fetchall()
                    return [row["file_path"] for row in rows]
            except Exception:
                return []
        return await asyncio.to_thread(_get)

    async def async_remove_local_art_by_path(self, path: str) -> bool:
        """Remove a local_art entry by file path (stale entry cleanup)."""
        await self._ensure_db()
        def _remove():
            try:
                with self._get_db() as conn:
                    conn.execute("DELETE FROM local_art WHERE file_path = ?", (path,))
                    conn.commit()
                    return True
            except Exception:
                return False
        return await asyncio.to_thread(_remove)

    async def async_remove_duplicate_local_art(self) -> int:
        """Remove duplicate local_art entries (keep newest per file_path). Returns count removed."""
        await self._ensure_db()
        def _dedup():
            try:
                with self._get_db() as conn:
                    # Keep the row with the highest rowid for each file_path
                    cursor = conn.execute(
                        "DELETE FROM local_art WHERE rowid NOT IN "
                        "(SELECT MAX(rowid) FROM local_art GROUP BY file_path)"
                    )
                    removed = cursor.rowcount
                    conn.commit()
                    return removed
            except Exception:
                return 0
        return await asyncio.to_thread(_dedup)

    async def async_purge_database(self) -> None:
        """Wipe all library and local metadata while keeping connection tokens."""
        if not self._db_path:
            return

        def _purge():
            import sqlite3
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute("DELETE FROM art_library")
                    conn.execute("DELETE FROM local_art")
                    conn.commit()
            except Exception as e:
                _LOGGER.error("Failed to purge database: %s", e)

        await asyncio.to_thread(_purge)


