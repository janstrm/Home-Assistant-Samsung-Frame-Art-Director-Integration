"""Async wrapper for samsungtvws client used by the integration.

This wrapper encapsulates connection, pairing (token), DUID retrieval,
and basic Art Mode controls in async methods compatible with Home Assistant.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Optional

# Suppress local HTTPS cert warnings from TV endpoints during pairing/info calls
try:  # pragma: no cover - best-effort suppression
    import urllib3  # type: ignore

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

_LOGGER = logging.getLogger(__name__)


def _mask_secret(value: Optional[str]) -> str:
    """Mask a secret value for logs."""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


class PairingTimeoutError(Exception):
    """Raised when pairing handshake did not complete in time."""


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

    def set_db_path(self, path: str) -> None:
        self._db_path = path

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
                    # Create table if not exists (base schema)
                    conn.execute(
                        """
            CREATE TABLE IF NOT EXISTS art_library (
                content_id TEXT PRIMARY KEY,
                width INTEGER,
                height INTEGER,
                date_added TIMESTAMP,
                last_seen TIMESTAMP,
                deleted_at TIMESTAMP,
                source TEXT
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
        
                    # Migration: Add columns if missing
                    existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(art_library)")]
                    _LOGGER.debug("DB Sync: art_library columns: %s", existing_cols)
                    
                    if "tags" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'tags' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN tags TEXT")
                    if "category" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'category' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN category TEXT")
                    if "deleted_at" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'deleted_at' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN deleted_at TEXT")
                    if "source_file" not in existing_cols:
                        _LOGGER.info("DB Sync: adding 'source_file' column to art_library")
                        conn.execute("ALTER TABLE art_library ADD COLUMN source_file TEXT")
                    
                    # Migration: local_art
                    local_cols = [row[1] for row in conn.execute("PRAGMA table_info(local_art)")]
                    if "is_favorite" not in local_cols:
                         _LOGGER.info("DB Sync: adding 'is_favorite' column to local_art")
                         conn.execute("ALTER TABLE local_art ADD COLUMN is_favorite INTEGER DEFAULT 0")

                    conn.commit()
            except Exception as e:
                _LOGGER.error("DB Init failed: %s", e)

        await asyncio.to_thread(_init_db)



    async def async_track_art(self, content_id: str, tags: Optional[str] = None, source_file: Optional[str] = None) -> None:
        """Track a new upload in the local DB with optional tags and source_file."""
        if not self._db_path or not content_id:
            return
        
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
                 
                 # Check local_art
                 curr_local = conn.execute("SELECT is_favorite FROM local_art WHERE file_path=?", (content_id,)).fetchone()
                 if curr_local:
                     new_val = 1 if not curr_local[0] else 0
                     conn.execute("UPDATE local_art SET is_favorite=? WHERE file_path=?", (new_val, content_id))
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

    async def async_delete_art(self, content_id: str) -> bool:
        """Permanently delete an item from disk and DB."""
        if not self._db_path or not content_id:
            return False
        
        await self._ensure_db()

        def _delete():
            import sqlite3
            import os
            try:
                # 1. Resolve path (if it's a file path)
                file_path = content_id if os.path.exists(content_id) else None
                
                with sqlite3.connect(self._db_path) as conn:
                    # If not explicitly a path, check DB for source_file or file_path
                    if not file_path:
                        row = conn.execute("SELECT file_path FROM local_art WHERE file_path=?", (content_id,)).fetchone()
                        if row: file_path = row[0]
                    
                    if not file_path:
                        row = conn.execute("SELECT source_file FROM art_library WHERE content_id=?", (content_id,)).fetchone()
                        if row: file_path = row[0]

                    # 2. Delete File (Safety check: must be in allowable dirs or look like art)
                    # We assume anything tracked in local_art is safe to delete if user requested it.
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            _LOGGER.info("Deleted file: %s", file_path)
                        except Exception as e:
                            _LOGGER.warning("Failed to delete file %s: %s", file_path, e)

                    # 3. Delete from DB
                    conn.execute("DELETE FROM local_art WHERE file_path=?", (content_id,))
                    conn.execute("DELETE FROM art_library WHERE content_id=?", (content_id,))
                    # Also try deleting by path if content_id was a path
                    if file_path and file_path != content_id:
                        conn.execute("DELETE FROM local_art WHERE file_path=?", (file_path,))
                        conn.execute("DELETE FROM art_library WHERE content_id=?", (file_path,))
                        
                    conn.commit()
                    return True
            except Exception as e:
                _LOGGER.error("Delete failed for %s: %s", content_id, e)
                return False

        return await asyncio.to_thread(_delete)

    async def async_get_thumbnail(self, content_id: str) -> bytes | None:
        """Get bytes for a thumbnail (local file) given an ID."""
        if not self._db_path: return None
        await self._ensure_db()
        
        def _get():
            import sqlite3
            import os
            try:
                # 1. Lookup local path from DB
                path = None
                with sqlite3.connect(self._db_path) as conn:
                    # Check art_library (source_file)
                    row = conn.execute("SELECT source_file FROM art_library WHERE content_id=?", (content_id,)).fetchone()
                    if row and row[0]:
                        path = row[0]
                    # Check local_art (file_path) if it looks like a path
                    if not path:
                         # Maybe content_id IS the path?
                         if os.path.exists(content_id):
                             path = content_id
                
                if path and os.path.exists(path):
                    # In a real implementation, we'd resize this here and cache it!
                    # For now, return the full image (Dashboard will scale it, but bandwidth heavy)
                    with open(path, "rb") as f:
                        return f.read()
            except Exception as e:
                _LOGGER.warning("Thumbnail fetch failed for %s: %s", content_id, e)
            return None
            
        return await asyncio.to_thread(_get)

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
                        path = r[0]
                        # Ensure valid JSON string by replacing backslashes
                        path = path.replace("\\", "/")
                        # For local files, ID is the path
                        items.append({
                            "id": path, 
                            "tags": r[1] or "",
                            "is_favorite": bool(r[2]) if has_fav else False,
                            "type": "local",
                            "source": path
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
                    # Upload first
                    path = winner['path']
                    try:
                        def _read():
                            with open(path, "rb") as f:
                                return f.read()
                        img_data = await asyncio.to_thread(_read)
                    except FileNotFoundError:
                        _LOGGER.warning(
                            "Rotate: Local file missing (stale DB entry), skipping: %s", path
                        )
                        filtered.remove(winner)
                        if not filtered:
                            _LOGGER.warning("Rotate: No valid candidates left after removing stale entries")
                            return False
                        continue

                    _LOGGER.debug("Rotate: uploading local item: %s", path)
                    await self.async_upload_image(img_data, matte=matte, source_file=path)
                    
                    # Note: async_upload_image does not return ID easily in all paths, 
                    # but it DOES select the image after upload.
                    # So we are done!
                    return True
                    
            except Exception as e:
                _LOGGER.error("Rotate: Action failed: %s", e)
                return False
        
        _LOGGER.warning("Rotate: Could not find a valid image after %d attempts", max_attempts)
        return False
        
    async def _async_select_image_id(self, content_id: str, matte: str = "none") -> None:
        """Helper to select an image by ID (best effort)."""
         # Fallback logic similar to upload select
        def _do_select():
            from samsungtvws import SamsungTVWS
            try:
                art_client = tv.art()
                # CRITICAL: For change_matte and 3.0.5, "none" is often the literal string expected,
                # but select_image prefers None to clear it.
                tv_matte = matte if matte else "none"
                try:
                    # For select_image, we use None for "none"
                    sel_matte = None if tv_matte == "none" else tv_matte
                    art_client.select_image(content_id, show=True, matte=sel_matte)
                except TypeError:
                    art_client.select_image(content_id, show=True)
                    # Secondary fallback: use change_matte
                    if hasattr(art_client, "change_matte"):
                        try:
                            # Apply to both landscape and portrait. 
                            # Try passing None if it's "none" just in case the string is not recognized.
                            final_matte = None if tv_matte == "none" else tv_matte
                            art_client.change_matte(content_id, matte_id=final_matte, portrait_matte=final_matte)
                        except Exception:
                            # If None fails, try the string "none"
                            if tv_matte == "none":
                                try:
                                    art_client.change_matte(content_id, matte_id="none", portrait_matte="none")
                                except Exception:
                                    pass
            except Exception as e:
                _LOGGER.debug("Select failed: %s", e)
            finally:
                try:
                    c = getattr(tv, "close", None)
                    if callable(c):
                        c()
                except Exception:
                    pass
                 
        await asyncio.to_thread(_do_select)

    async def async_rotate_from_folder(self, source_dir: str, matte: str = "none") -> bool:
        """Rotate art by picking a random file from a folder and uploading it."""
        if not source_dir:
            return False

        def _pick_and_read():
            import os
            import random
            path = os.path.expanduser(source_dir)
            if not os.path.isdir(path):
                # Try relative to /media if not absolute/found
                if not path.startswith("/") and os.path.isdir("/media/frame/library"): # simple fallback check
                     alt = os.path.join("/media/frame/library", path)
                     if os.path.isdir(alt): path = alt
            
            path = os.path.abspath(path)
            allowed_media = os.path.abspath("/media")
            allowed_config = os.path.abspath(self.hass.config.path())
            if not path.startswith(allowed_media) and not path.startswith(allowed_config):
                _LOGGER.error("Rotate(folder): Path traversal detected or unallowed path: %s", path)
                return None, None

            if not os.path.exists(path):
                _LOGGER.warning("Rotate(folder): path %s does not exist", path)
                return None, None

            exts = {".jpg", ".jpeg", ".png", ".webp"}
            files = []
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        if entry.is_file() and os.path.splitext(entry.name)[1].lower() in exts:
                            files.append(entry.path)
            except Exception as e:
                _LOGGER.warning("Rotate(folder): error scanning %s: %s", path, e)
                return None, None

            if not files:
                 _LOGGER.warning("Rotate(folder): no images in %s", path)
                 return None, None
            
            f = random.choice(files)
            try:
                with open(f, "rb") as fh:
                    return f, fh.read()
            except Exception as e:
                _LOGGER.warning("Rotate(folder): read error %s: %s", f, e)
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
        """Connect and pair using token_file in a background thread, then persist token."""
        _LOGGER.debug("Client: connect_and_pair start host=%s token_present=%s", self._host, bool(self._token))
        # Resolve token file path (under /config/pairing_tokens by caller)
        token_path: Optional[str] = self._token_file_path
        if not token_path:
            temp_dir = tempfile.mkdtemp(prefix="ha_samsungtvws_")
            token_path = os.path.join(temp_dir, "tv-token.txt")
        else:
            try:
                os.makedirs(os.path.dirname(token_path), exist_ok=True)
            except Exception:
                pass

        def _blocking_pair_and_info(port: int) -> dict:
            from samsungtvws import SamsungTVWS  # type: ignore
            tv = SamsungTVWS(self._host, port=port, token_file=token_path, name=self._client_name)
            try:
                # Trigger auth and wait for acceptance
                try:
                    tv.art().supported()
                except Exception:
                    pass
                try:
                    return tv.rest_device_info()
                except Exception:
                    return {}
            finally:
                try:
                    close_fn = getattr(tv, "close", None)
                    if callable(close_fn):
                        close_fn()
                except Exception:
                    pass

        info: dict = {}
        try:
            info = await asyncio.wait_for(asyncio.to_thread(_blocking_pair_and_info, 8002), timeout=120)
        except Exception:
            info = {}
        if not info:
            try:
                info = await asyncio.wait_for(asyncio.to_thread(_blocking_pair_and_info, 8001), timeout=60)
            except Exception:
                info = {}
        if not info:
            _LOGGER.warning("Client: pairing/info failed or timed out for %s", self._host)
            self._connected = False
            return

        self._connected = True
        self._duid = info.get("device", {}).get("duid")
        _LOGGER.debug("Client: device info fetched host=%s has_info=%s", self._host, bool(info))

        token_value: Optional[str] = None
        if token_path and os.path.exists(token_path):
            try:
                token_value = (await asyncio.to_thread(lambda p=token_path: open(p, "r", encoding="utf-8").read())).strip() or None
            except Exception:
                token_value = None

        if token_value:
            self._token = token_value
            _LOGGER.info("Client: token captured host=%s token=%s", self._host, _mask_secret(self._token))

        duid = None
        try:
            duid = info.get("device", {}).get("duid") if isinstance(info, dict) else None
        except Exception:
            duid = None
        if not duid and self._token:
            def _get_info_with_token() -> dict:
                from samsungtvws import SamsungTVWS  # type: ignore
                tv2 = SamsungTVWS(self._host, token=self._token, name=self._client_name)  # type: ignore[arg-type]
                try:
                    return tv2.rest_device_info()
                finally:
                    try:
                        close_fn = getattr(tv2, "close", None)
                        if callable(close_fn):
                            close_fn()
                    except Exception:
                        pass

            try:
                info2 = await asyncio.wait_for(asyncio.to_thread(_get_info_with_token), timeout=10)
                duid = info2.get("device", {}).get("duid") if isinstance(info2, dict) else None
            except Exception:
                duid = None

        self._duid = duid
        self._connected = True

        # Cleanup: delete token file now that token is captured
        try:
            if token_path and os.path.exists(token_path):
                os.remove(token_path)
        except Exception:
            pass

        if not self._duid and not self._token:
            raise PairingTimeoutError(f"Pairing timed out for {self._host}")
        _LOGGER.info("Client: paired host=%s duid=%s", self._host, self._duid)

    async def async_disconnect(self) -> None:
        if self._connected:
            _LOGGER.debug("Disconnecting from Samsung Frame")
            await asyncio.sleep(0.05)
            self._connected = False

    async def async_get_artmode_status(self) -> Optional[str]:
        """Return current Art Mode status as 'on'/'off'/None with best effort logging."""
        try:
            from samsungtvws import SamsungTVWS  # type: ignore
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("get_artmode: samsungtvws unavailable: %r", err)
            return None

        def _read_status() -> Optional[str]:
            tv = None
            try:
                if self._token:
                    tv = SamsungTVWS(self._host, port=(self._port or 8002), token=self._token, name=self._client_name)  # type: ignore[arg-type]
                else:
                    tv = SamsungTVWS(self._host, port=(self._port or 8002), name=self._client_name)
            except TypeError:
                tv = SamsungTVWS(self._host)
            except (ConnectionError, TimeoutError, OSError) as e:
                _LOGGER.debug("get_artmode: connection error on %s: %r", self._host, e)
                return None
            try:
                status = tv.art().get_artmode()
                return str(status).lower() if status is not None else None
            except (ConnectionError, TimeoutError, ValueError, OSError) as e:
                _LOGGER.debug("get_artmode: request error on %s: %r", self._host, e)
                return None
            finally:
                if tv is not None:
                    try:
                        close_fn = getattr(tv, "close", None)
                        if callable(close_fn):
                            close_fn()
                    except (ConnectionError, OSError):
                        pass

        try:
            status = await asyncio.wait_for(asyncio.to_thread(_read_status), timeout=10)
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
            from samsungtvws import SamsungTVWS
        except Exception:
            return results

        def _fetch():
            tv = None
            try:
                tv = SamsungTVWS(self._host, port=(self._port or 8002), token=self._token, name=self._client_name) if self._token else SamsungTVWS(self._host, port=(self._port or 8002), name=self._client_name)
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
                                row = conn.execute("SELECT source_file FROM art_library WHERE content_id = ?", (results["content_id"],)).fetchone()
                                if row and row[0] and os.path.isfile(row[0]):
                                    _LOGGER.debug("Art Preview: using local file lookup for %s", results["content_id"])
                                    with open(row[0], "rb") as f:
                                        results["image"] = f.read()
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
                    get_photo_fn = getattr(tv.art(), "get_photo", None)
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
                    try:
                        tv.close()
                    except Exception:
                        pass

        try:
            await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=15)
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
            current = await self.async_get_artmode_status()
            if current is not None:
                if bool(enabled) and current in ("on", "true", "1"):
                    _LOGGER.debug("ArtMode: already on for %s; skipping", self._host)
                    return
                if not bool(enabled) and current in ("off", "false", "0", "none"):
                    _LOGGER.debug("ArtMode: already off for %s; skipping", self._host)
                    return
        except Exception:
            pass
        # Preferred path: use AsyncRemote if available
        try:
            from samsungtvws.async_remote import SamsungTVWSAsyncRemote  # type: ignore

            async def _async_set_with_async_remote() -> None:
                timeout = 31
                async with SamsungTVWSAsyncRemote(
                    host=self._host,
                    port=self._port or 8002,
                    token=self._token or "",
                    name=self._client_name,
                    timeout=timeout,
                ) as remote:
                    # Some installed versions do not expose remote.art(); skip if missing
                    if not hasattr(remote, "art"):
                        raise AttributeError("AsyncRemote missing art() API")
                    await remote.open()
                    _LOGGER.debug("ArtMode(async): sending set_artmode(%s) to %s", bool(enabled), self._host)

                    def _do_set() -> None:
                        remote.art().set_artmode(bool(enabled))

                    await asyncio.to_thread(_do_set)

                    # Verify a few times while the remote context is open
                    for attempt in range(1, 3 + 1):
                        try:
                            status = await asyncio.to_thread(lambda: remote.art().get_artmode())
                            _LOGGER.debug("ArtMode(async): attempt %s status=%s on %s", attempt, status, self._host)
                            if bool(enabled) and str(status).lower() in ("on", "true", "1"):
                                return
                            if not bool(enabled) and str(status).lower() in ("off", "false", "0", "none"):
                                return
                        except Exception:  # noqa: BLE001
                            _LOGGER.debug("ArtMode(async): verification not available at attempt %s on %s", attempt, self._host)
                        await asyncio.sleep(2)

            await _async_set_with_async_remote()
            return
        except Exception as async_err:  # noqa: BLE001
            _LOGGER.debug("AsyncRemote not available or failed, falling back to sync: %r", async_err, exc_info=True)

        # Fallback path: sync client in executor
        try:
            from samsungtvws import SamsungTVWS  # type: ignore
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("samsungtvws import failed, cannot set art mode: %s", err)
            await asyncio.sleep(0.01)
            return

        def _make_client():
            try:
                if self._token:
                    _LOGGER.debug("Creating client with stored token: %s", _mask_secret(self._token))
                    if self._port:
                        return SamsungTVWS(self._host, port=self._port, token=self._token, name=self._client_name)  # type: ignore[arg-type]
                    return SamsungTVWS(self._host, token=self._token, name=self._client_name)  # type: ignore[arg-type]
            except TypeError:
                pass
            try:
                if self._port:
                    return SamsungTVWS(self._host, port=self._port, name=self._client_name)
                return SamsungTVWS(self._host, name=self._client_name)
            except TypeError:
                return SamsungTVWS(self._host)

        def _set():
            tv_local = _make_client()
            _LOGGER.debug("ArtMode: sending set_artmode(%s) to %s", bool(enabled), self._host)
            import time
            last_status = None
            # Precompute selection candidate once to reduce available() calls
            selection_candidate = None
            try:
                try:
                    current = tv_local.art().get_current()
                except Exception:
                    current = None
                if isinstance(current, dict):
                    selection_candidate = current.get("content_id") or current.get("contentId")
                if not selection_candidate:
                    try:
                        avail = tv_local.art().available() or []
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
                    tv_local.art().set_artmode(bool(enabled))
                except Exception as exc:  # noqa: BLE001
                    # Continue with verification/select fallback even if the set call fails
                    _LOGGER.warning("ArtMode: set_artmode(%s) failed on %s: %r", bool(enabled), self._host, exc)

            except Exception:  # noqa: BLE001
                pass

            # Verification + fallback loop (up to ~10s)
            for attempt in range(1, 3 + 1):
                try:
                    status = tv_local.art().get_artmode()
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
                            tv_local.art().select_image(selection_candidate, show=True)
                    except Exception as sel_err:
                        _LOGGER.debug("ArtMode: select image fallback failed on %s: %r", self._host, sel_err)
                time.sleep(2)
            _LOGGER.debug("ArtMode: final status=%s on %s", last_status, self._host)
            try:
                close_fn = getattr(tv_local, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception:
                pass

        await asyncio.to_thread(_set)

    async def async_preprocess_image(self, image_bytes: bytes) -> bytes:
        """Resize to 3840x2160 with center-crop and return JPEG bytes."""
        try:
            from PIL import Image
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Pillow not available: %s", err)
            raise

        def _process() -> bytes:
            with Image.open(io.BytesIO(image_bytes)) as im:  # type: ignore
                # Convert to RGB
                if im.mode not in ("RGB", "RGBA"):
                    im_converted = im.convert("RGB")
                else:
                    im_converted = im.convert("RGB")

                target_w, target_h = 3840, 2160
                src_w, src_h = im_converted.width, im_converted.height
                src_ratio = src_w / src_h
                tgt_ratio = target_w / target_h

                # Scale to fill, then center-crop
                if src_ratio > tgt_ratio:
                    # Source wider than target: height fit
                    scale = target_h / src_h
                    new_w = int(src_w * scale)
                    new_h = target_h
                else:
                    # Source taller/narrower: width fit
                    scale = target_w / src_w
                    new_w = target_w
                    new_h = int(src_h * scale)

                resized = im_converted.resize((new_w, new_h), Image.LANCZOS)
                left = (new_w - target_w) // 2
                top = (new_h - target_h) // 2
                right = left + target_w
                bottom = top + target_h
                cropped = resized.crop((left, top, right, bottom))

                out = io.BytesIO()
                cropped.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
                return out.getvalue()

        import io

        return await asyncio.to_thread(_process)

    async def async_upload_image(self, image_bytes: bytes, matte: str = "none", source_file: Optional[str] = None) -> None:
        """Upload preprocessed image to the TV and select it with optional matte."""
        processed = await self.async_preprocess_image(image_bytes)
        _LOGGER.debug("Upload: processed image size=%s bytes for host=%s", len(processed), self._host)

        # Optional preflight removed to reduce chatter; rely on upload errors for feedback

        # Preferred path: use async art API when available to avoid sync websocket stalls
        async def _async_upload_once() -> Optional[str]:
            try:
                try:
                    from samsungtvws import SamsungTVAsyncArt  # type: ignore
                except ImportError:
                    from samsungtvws.async_art import SamsungTVAsyncArt  # type: ignore
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("Async art API unavailable: %r", e)
                return None

            tv_async = None
            
            async def _attempt_upload(port):
                async_client = None
                try:
                    kwargs: dict = {
                        "host": self._host,
                        "port": port,
                        "name": self._client_name,
                    }
                    if self._token:
                        kwargs["token"] = self._token
                    elif self._token_file_path:
                        kwargs["token_file"] = self._token_file_path

                    def _build_async_client():
                        try:
                            from samsungtvws import SamsungTVAsyncArt  # type: ignore
                        except ImportError:
                            from samsungtvws.async_art import SamsungTVAsyncArt  # type: ignore
                        return SamsungTVAsyncArt(**kwargs)  # type: ignore[arg-type]

                    async_client = await asyncio.to_thread(_build_async_client)
                    
                    # Prime it
                    try:
                        await asyncio.wait_for(async_client.supported(), timeout=5)
                    except Exception:
                        pass

                    _LOGGER.debug("Upload(async): attempting port %s on %s", port, self._host)
                    new_id = await asyncio.wait_for(
                        async_client.upload(processed, file_type="JPEG", matte=matte), timeout=45
                    )
                    
                    if not new_id:
                        raise ValueError("No content ID returned from TV")

                    # Select image
                    tv_matte = matte if matte else "none"
                    try:
                        sel_matte = None if tv_matte == "none" else tv_matte
                        await asyncio.wait_for(async_client.select_image(new_id, show=True, matte=sel_matte), timeout=15)
                    except (TypeError, Exception):
                        # Fallback for older select_image or 3.0.5 matte quirk
                        await asyncio.wait_for(async_client.select_image(new_id, show=True), timeout=10)
                        if hasattr(async_client, "change_matte"):
                            final_matte = "none" if tv_matte == "none" else tv_matte
                            await asyncio.wait_for(async_client.change_matte(new_id, matte_id=final_matte, portrait_matte=final_matte), timeout=10)

                    return new_id, async_client
                except Exception:
                    # Cleanup if failed
                    if async_client:
                        try:
                            # Close if possible (though 3.0.5 might not have it)
                            close_fn = getattr(async_client, "close", None)
                            if callable(close_fn):
                                await close_fn()
                        except Exception:
                            pass
                    raise

            # --- Try 8002 (SSL) then 8001 (Non-SSL) ---
            new_content_id = None
            final_client = None
            try:
                new_content_id, final_client = await _attempt_upload(8002)
            except Exception as e:
                _LOGGER.debug("Upload(async): Port 8002 failed, retrying 8001: %r", e)
                try:
                    new_content_id, final_client = await _attempt_upload(8001)
                except Exception as e2:
                    _LOGGER.debug("Upload(async): Both ports failed for %s: %r", self._host, e2)
                    return None

            try:
                # Track and finish
                await self.async_track_art(new_content_id, source_file=source_file)
                
                # Final cleanup
                if final_client:
                    try:
                        close_fn = getattr(final_client, "close", None)
                        if callable(close_fn):
                            await close_fn()
                    except Exception:
                        pass
                
                return str(new_content_id)
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("Upload(async) failed on %s: %r", self._host, e)
                return None
            finally:
                # final_client already closed in success path or _attempt_upload error path
                pass

        try:
            from samsungtvws import SamsungTVWS  # type: ignore
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("samsungtvws import failed, cannot upload image: %s", err)
            return

        def _make_client():
            try:
                if self._token:
                    # Force SSL websocket port 8002 for upload operations
                    return SamsungTVWS(self._host, port=8002, token=self._token, name=self._client_name)  # type: ignore[arg-type]
            except TypeError:
                pass
            try:
                # Force SSL websocket port 8002 for upload operations
                return SamsungTVWS(self._host, port=8002, name=self._client_name)
            except TypeError:
                return SamsungTVWS(self._host)

        tv = await asyncio.to_thread(_make_client)

        def _upload_once():
            try:
                _LOGGER.debug("Upload: starting art.upload on %s (matte=%s)", self._host, matte)
                # Pass matte to upload so it applies immediately if supported
                remote_filename = tv.art().upload(processed, file_type="JPEG", matte=matte)
                _LOGGER.debug("Upload: art.upload returned filename=%s on %s", remote_filename, self._host)
                if remote_filename:
                    # CRITICAL: For change_matte and 3.0.5, "none" is the literal string expected,
                    # but select_image prefers None to clear it.
                    tv_matte = matte if matte else "none"
                    art_client = tv.art()
                    try:
                        # For select_image, we use None for "none"
                        sel_matte = None if tv_matte == "none" else tv_matte
                        art_client.select_image(remote_filename, show=True, matte=sel_matte)
                        _LOGGER.debug("Upload: select_image success on %s (matte=%s)", self._host, tv_matte)
                    except TypeError:
                        # Fallback for older library versions that don't support 'matte' keyword
                        _LOGGER.debug("Upload: select_image does not support 'matte' keyword, falling back")
                        art_client.select_image(remote_filename, show=True)
                        # Secondary fallback: use change_matte which is supported in 3.0.5
                        if hasattr(art_client, "change_matte"):
                            try:
                                # Apply to both landscape and portrait. 
                                # Use literal "none" string to force removal. None often implies "no change".
                                final_matte = "none" if tv_matte == "none" else tv_matte
                                art_client.change_matte(remote_filename, matte_id=final_matte, portrait_matte=final_matte)
                            except Exception as e:
                                _LOGGER.debug("Upload: change_matte failed: %r", e)
                    except Exception as e:
                        _LOGGER.debug("Upload: select_image failed for %s: %r", remote_filename, e)
                    
                    return remote_filename
            finally:
                try:
                    close_fn = getattr(tv, "close", None)
                    if callable(close_fn):
                        close_fn()
                except Exception:
                    pass

        async with self._art_lock:
            # Try async upload first (up to 2 attempts) before falling back to sync path
            for attempt_async in range(1, 2 + 1):
                new_id = await _async_upload_once()
                if new_id:
                    _LOGGER.info("Upload(success, async) on host=%s (attempt %s)", self._host, attempt_async)
                    try:
                        diag_ok = await self.async_art_diagnostics(max_ids=1)
                        _LOGGER.debug("Upload(async) post-check on %s: current=%s", self._host, diag_ok.get("current"))
                    except Exception:
                        pass
                    return
                if attempt_async < 2:
                    await asyncio.sleep(1.0)

            # Retry a few times on transient art channel ConnectionFailure
            backoff_seconds = [0.75, 1.5, 2.5, 4.0]
            for attempt in range(1, 5 + 1):
                try:
                    # Prime art channel just before attempt
                    try:
                        from samsungtvws import SamsungTVWS  # type: ignore
                        def _prime():
                            try:
                                tvp = SamsungTVWS(self._host, port=(self._port or 8002), token=self._token, name=self._client_name) if self._token else SamsungTVWS(self._host, port=(self._port or 8002), name=self._client_name)  # type: ignore[arg-type]
                            except TypeError:
                                tvp = SamsungTVWS(self._host)
                            try:
                                try:
                                    tvp.art().supported()
                                except Exception:
                                    pass
                                try:
                                    tvp.art().get_artmode()
                                except Exception:
                                    pass
                            finally:
                                try:
                                    c = getattr(tvp, "close", None)
                                    if callable(c):
                                        c()
                                except Exception:
                                    pass
                        await asyncio.to_thread(_prime)
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass

                    res = await asyncio.wait_for(asyncio.to_thread(_upload_once), timeout=120)
                    _LOGGER.info("Upload success on host=%s (attempt %s, content_id=%s)", self._host, attempt, res)
                    if res:
                        await self.async_track_art(res, source_file=source_file)
                    try:
                        # Confirm selection by logging current content id
                        diag_ok = await self.async_art_diagnostics(max_ids=1)
                        _LOGGER.debug("Upload post-check on %s: current=%s", self._host, diag_ok.get("current"))
                    except Exception:
                        pass
                    break
                except asyncio.TimeoutError:
                    _LOGGER.warning("Upload timed out on host=%s (attempt %s)", self._host, attempt)
                    if attempt >= 5:
                        raise
                except Exception as exc:  # noqa: BLE001
                    # Detect samsungtvws ConnectionFailure without importing globally
                    exc_name = type(exc).__name__
                    if exc_name == "ConnectionFailure" and attempt < 5:
                        _LOGGER.debug("Upload ConnectionFailure on %s, retrying (attempt %s)", self._host, attempt)
                        await asyncio.sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)])
                        # Recreate client for a clean connection next try
                        tv = await asyncio.to_thread(_make_client)
                        continue
                    raise

    async def async_art_diagnostics(self, max_ids: int = 10) -> dict:
        """Collect Art Mode diagnostics via samsungtvws.

        Returns dict with supported, status, current id, available sample ids.
        """
        try:
            from samsungtvws import SamsungTVWS  # type: ignore
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Diagnostics: samsungtvws unavailable: %s", err)
            return {"error": str(err)}

        def _collect() -> dict:
            try:
                if self._token:
                    tv = SamsungTVWS(self._host, port=(self._port or 8002), token=self._token, name=self._client_name)  # type: ignore[arg-type]
                else:
                    tv = SamsungTVWS(self._host, port=(self._port or 8002), name=self._client_name)
            except TypeError:
                tv = SamsungTVWS(self._host)
            result: dict = {"host": self._host}
            try:
                result["supported"] = tv.art().supported()
            except Exception as e:  # noqa: BLE001
                result["supported_error"] = repr(e)
            try:
                status = tv.art().get_artmode()
                result["status"] = status
            except Exception as e:  # noqa: BLE001
                result["status_error"] = repr(e)
            try:
                current = tv.art().get_current()
                result["current"] = current
            except Exception as e:  # noqa: BLE001
                result["current_error"] = repr(e)
            try:
                avail = tv.art().available() or []
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

        data = await asyncio.to_thread(_collect)
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
            try:
                tv = SamsungTVWS(self._host, port=(self._port or 8002), token=self._token, name=self._client_name) if self._token else SamsungTVWS(self._host, port=(self._port or 8002), name=self._client_name)  # type: ignore[arg-type]
            except TypeError:
                tv = SamsungTVWS(self._host)
            current_id = None
            available: list = []
            try:
                cur = tv.art().get_current()
                if isinstance(cur, dict):
                    current_id = cur.get("content_id") or cur.get("contentId")
            except Exception:
                current_id = None
            try:
                available = tv.art().available() or []
            except Exception:
                available = []
            try:
                closer = getattr(tv, "close", None)
                if callable(closer):
                    closer()
            except Exception:
                pass
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

        current_id, on_tv_ids = await asyncio.to_thread(_fetch_tv_state)

        # If only_integration_managed: filter via DB entries we know about
        candidates: list[str] = list(on_tv_ids)
        skipped_favorites: list[str] = []
        db_rows: dict[str, dict] = {}

        if only_integration_managed and self._db_path:
            import sqlite3
            def _db_fetch(ids: list[str]) -> dict[str, dict]:
                if not ids:
                    return {}
                placeholders = ",".join(["?"] * len(ids))
                q = (
                    f"SELECT content_id, is_favorite, created_at, last_displayed_at, on_tv FROM art_library "
                    f"WHERE content_id IN ({placeholders})"
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
                            }
                    finally:
                        conn.close()
                except Exception as e:  # noqa: BLE001
                    _LOGGER.debug("Cleanup: DB fetch failed: %r", e)
                return out
            db_rows = await asyncio.to_thread(_db_fetch, candidates)
            # Restrict candidates to items we know and track
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
            excess = max(0, len(on_tv_ids) - int(max_items))
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
            try:
                from samsungtvws import SamsungTVWS  # type: ignore
            except Exception as err:  # noqa: BLE001
                summary["errors"].append(str(err))
                return summary

            def _delete(ids: list[str]) -> tuple[list[str], list[str]]:
                deleted: list[str] = []
                errors: list[str] = []
                if not ids:
                    return deleted, errors
                try:
                    tv = SamsungTVWS(self._host, port=(self._port or 8002), token=self._token, name=self._client_name) if self._token else SamsungTVWS(self._host, port=(self._port or 8002), name=self._client_name)  # type: ignore[arg-type]
                except TypeError:
                    tv = SamsungTVWS(self._host)
                try:
                    batch = list(ids)
                    # Prefer delete_list if present
                    try:
                        tv.art().delete_list(batch)
                        deleted = batch
                    except Exception:
                        # Fallback: delete one by one
                        for cid in batch:
                            try:
                                tv.art().delete(cid)
                                deleted.append(cid)
                            except Exception as e:  # noqa: BLE001
                                errors.append(f"{cid}: {e!r}")
                finally:
                    try:
                        closer = getattr(tv, "close", None)
                        if callable(closer):
                            closer()
                    except Exception:
                        pass
                return deleted, errors

            deleted, errs = await asyncio.to_thread(_delete, to_delete)
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

    async def async_rotate_art_now(self, *, mode: str = "local", source_dir: str = "/media/frame/library", matte: str = "none") -> None:
        """Rotate displayed art according to mode.

        Supported now:
        - local: pick random file from source_dir and upload/select
        - tv: pick a random available TV artwork and select
        Placeholders (no-op for now): library, aware
        """
        mode = (mode or "local").lower()
        if mode == "local":
            import os
            import random
            try:
                files = [
                    os.path.join(source_dir, f)
                    for f in os.listdir(source_dir)
                    if os.path.isfile(os.path.join(source_dir, f)) and f.lower().split(".")[-1] in ("jpg", "jpeg", "png")
                ]
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("rotate(local): unable to list %s: %r", source_dir, err)
                return
            if not files:
                _LOGGER.info("rotate(local): no images found in %s", source_dir)
                return
            pick = random.choice(files)
            _LOGGER.debug("rotate(local): picked %s", pick)
            # Read bytes and reuse upload helper (preprocess + select)
            def _read(p: str) -> bytes:
                with open(p, "rb") as f:
                    return f.read()
            image_bytes = await asyncio.to_thread(_read, pick)
            await self.async_upload_image(image_bytes, matte=matte, source_file=pick)
            return

        if mode == "tv":
            try:
                from samsungtvws import SamsungTVWS  # type: ignore
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("rotate(tv): samsungtvws unavailable: %s", err)
                return

            def _select_random_from_tv():
                try:
                    tv = SamsungTVWS(self._host, port=(self._port or 8002), token=self._token, name=self._client_name) if self._token else SamsungTVWS(self._host, port=(self._port or 8002), name=self._client_name)  # type: ignore[arg-type]
                except TypeError:
                    tv = SamsungTVWS(self._host)
                try:
                    avail = tv.art().available() or []
                    candidates: list[str] = []
                    for item in avail:
                        cid = None
                        if isinstance(item, dict):
                            cid = item.get("id") or item.get("content_id") or item.get("contentId")
                        else:
                            cid = str(item)
                        if cid:
                            candidates.append(str(cid))
                    if not candidates:
                        return
                    import random as _rand
                    pick = _rand.choice(candidates)
                    tv_matte = matte if matte else "none"
                    art_client = tv.art()
                    try:
                        # For select_image, we use None for "none"
                        sel_matte = None if tv_matte == "none" else tv_matte
                        art_client.select_image(pick, show=True, matte=sel_matte)
                        _LOGGER.debug("rotate(tv): select_image success for %s", pick)
                    except TypeError:
                        _LOGGER.debug("rotate(tv): select_image does not support 'matte' keyword, falling back")
                        art_client.select_image(pick, show=True)
                        # Secondary fallback: use change_matte which is supported in 3.0.5
                        if hasattr(art_client, "change_matte"):
                            try:
                                # Apply to both landscape and portrait. 
                                # Try passing None if it's "none" just in case the string is not recognized.
                                final_matte = None if tv_matte == "none" else tv_matte
                                art_client.change_matte(pick, matte_id=final_matte, portrait_matte=final_matte)
                            except Exception:
                                # If None fails, try the string "none"
                                if tv_matte == "none":
                                    try:
                                        art_client.change_matte(pick, matte_id="none", portrait_matte="none")
                                    except Exception:
                                        pass
                    except Exception as e:
                        _LOGGER.debug("rotate(tv): select_image failed: %r", e)
                finally:
                    closer = getattr(tv, "close", None)
                    if callable(closer):
                        closer()

            async with self._art_lock:
                await asyncio.to_thread(_select_random_from_tv)
            return

        # Placeholders for future modes
        if mode in ("library", "aware"):
            _LOGGER.info("rotate(%s): mode not yet implemented; no action taken", mode)
            return
        _LOGGER.info("rotate(%s): unknown mode; no action taken", mode)


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


