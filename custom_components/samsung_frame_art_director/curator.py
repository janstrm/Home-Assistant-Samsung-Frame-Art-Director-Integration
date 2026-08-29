"""Curator module for AI-based image processing and organization.

This module handles:
1. Scanning the inbox folder.
2. Analyzing images using the configured AI provider.
3. Saving metadata (tags) to the local database.
4. Moving processed files to the library folder.
"""
import os
import shutil
import logging
from pathlib import Path

from PIL import Image

from .ai import create_analyzer
from .api import SamsungFrameClient
from .const import (
    AI_PROVIDER_GEMINI,
    CONF_AI_PROVIDER,
    CONF_AI_MODEL,
    CONF_GEMINI_API_KEY,
    CONF_OPENAI_API_KEY,
    CONF_INBOX_DIR,
    CONF_LIBRARY_DIR,
    DEFAULT_INBOX_DIR,
    DEFAULT_LIBRARY_DIR,
)
from .file_access import UnsafeLocalPathError, ensure_allowed_local_path

_LOGGER = logging.getLogger(__name__)

class ContentCurator:
    def __init__(self, hass, entry, api: SamsungFrameClient):
        self.hass = hass
        self.entry = entry
        self.api = api
        self._inbox_dir = entry.options.get(CONF_INBOX_DIR) or DEFAULT_INBOX_DIR
        self._library_dir = entry.options.get(CONF_LIBRARY_DIR) or DEFAULT_LIBRARY_DIR

    def _build_analyzer(self):
        """Build the AI analyzer for the configured provider.

        Returns ``(analyzer, error)``; ``error`` is ``None`` on success.
        """
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        provider = self.entry.options.get(CONF_AI_PROVIDER, AI_PROVIDER_GEMINI)
        return create_analyzer(
            provider,
            gemini_api_key=self.entry.options.get(CONF_GEMINI_API_KEY, ""),
            openai_api_key=self.entry.options.get(CONF_OPENAI_API_KEY, ""),
            model=self.entry.options.get(CONF_AI_MODEL, ""),
            session=async_get_clientsession(self.hass),
        )

    async def async_process_inbox(self):
        """Process all images in the inbox."""
        analyzer, analyzer_err = self._build_analyzer()
        if analyzer_err:
            _LOGGER.warning("Process Inbox: %s", analyzer_err)
            return {"count": 0, "error": analyzer_err}

        # Scan inbox (Moved to executor)
        def _list_files():
            inbox_dir = ensure_allowed_local_path(self.hass, self._inbox_dir)
            library_dir = ensure_allowed_local_path(self.hass, self._library_dir)
            inbox_dir.mkdir(parents=True, exist_ok=True)
            library_dir.mkdir(parents=True, exist_ok=True)
            files: list[Path] = []
            for entry in inbox_dir.iterdir():
                if entry.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                try:
                    candidate = ensure_allowed_local_path(self.hass, entry)
                except UnsafeLocalPathError:
                    _LOGGER.warning("Process Inbox: Skipping out-of-root image")
                    continue
                if candidate.is_file():
                    files.append(candidate)
            return library_dir, files

        try:
            library_dir, files = await self.hass.async_add_executor_job(_list_files)
        except Exception as e:
            _LOGGER.error("Process Inbox: Failed to scan inbox folder '%s': %s", self._inbox_dir, e)
            return {"count": 0, "error": f"Inbox scan failed: {e}"}

        if not files:
            _LOGGER.info("Process Inbox: Inbox folder '%s' is empty. Nothing to process.", self._inbox_dir)
            return {"count": 0}

        _LOGGER.info("Process Inbox: Found %d images in '%s'. Starting AI analysis...", len(files), self._inbox_dir)
        
        processed_count = 0
        skipped_count = 0
        
        for source_path in files:
            filename = source_path.name
            
            # 1. Analyze (Atomic: Stop here if fails)
            try:
                def _read_file():
                    trusted_source = ensure_allowed_local_path(self.hass, source_path)
                    with trusted_source.open("rb") as file_handle:
                        return file_handle.read()
                
                data = await self.hass.async_add_executor_job(_read_file)
                result = await analyzer.analyze_image(data, prompt="Describe this image")
                
                if "error" in result:
                    error_str = str(result['error'])
                    if "429" in error_str:
                        _LOGGER.warning(
                            "Process Inbox: Gemini API rate limit hit (429) while processing '%s'. "
                            "Stopping. %d images processed so far, %d remaining.",
                            filename, processed_count, len(files) - processed_count - skipped_count
                        )
                        break
                    _LOGGER.warning(
                        "Process Inbox: Gemini AI analysis failed for '%s': %s. Skipping this image.",
                        filename, error_str
                    )
                    skipped_count += 1
                    continue
                
                tags = ",".join(result.get("tags", []))
                description = result.get("description", "")
                
                _LOGGER.info("Process Inbox: AI tagged '%s' -> Tags: %s", filename, tags)

                # 2. Probe Metadata (Executor)
                def _probe():
                    trusted_source = ensure_allowed_local_path(self.hass, source_path)
                    with Image.open(trusted_source) as img:
                        w, h = img.size
                    return w, h, len(data)

                width, height, file_size = await self.hass.async_add_executor_job(_probe)

                # 3. Move to Library (Executor)
                def _move():
                    trusted_source = ensure_allowed_local_path(self.hass, source_path)
                    # Ensure unique filename in library
                    dest_filename = filename
                    counter = 1
                    dest_path = ensure_allowed_local_path(
                        self.hass, library_dir / dest_filename
                    )
                    while dest_path.exists():
                        name, ext = os.path.splitext(filename)
                        dest_filename = f"{name}_{counter}{ext}"
                        counter += 1
                        dest_path = ensure_allowed_local_path(
                            self.hass, library_dir / dest_filename
                        )

                    shutil.move(str(trusted_source), str(dest_path))
                    return str(dest_path)

                # CRITICAL: We move the file ONLY after AI analysis is successful
                dest_path = await self.hass.async_add_executor_job(_move)

            except Exception as e:
                _LOGGER.error("Process Inbox: Failed to analyze/move '%s': %s", filename, e)
                skipped_count += 1
                continue

            # 4. Update Database (Now file is moved, record it)
            try:
                await self.api.async_add_local_art(
                    file_path=dest_path,
                    tags=tags,
                    description=description,
                    width=width,
                    height=height,
                    file_size=file_size
                )
                processed_count += 1
                
            except Exception as e:
                _LOGGER.error(
                    "Process Inbox: File moved to '%s' but failed to save metadata to DB: %s. "
                    "Run 'Sync Library' to recover this image.",
                    dest_path, e
                )
                skipped_count += 1

        _LOGGER.info(
            "Process Inbox: Finished. Processed: %d, Skipped: %d, Total: %d",
            processed_count, skipped_count, len(files)
        )
        return {"count": processed_count, "skipped": skipped_count}

    async def async_sync_library(self):
        """Full bidirectional sync: remove stale entries, deduplicate, and add untracked files."""
        _LOGGER.info("Sync Library: Starting full sync...")

        # ── Phase 1: Remove duplicates ──────────────────────────────────
        dupes_removed = await self.api.async_remove_duplicate_local_art()
        if dupes_removed > 0:
            _LOGGER.info("Sync Library: Removed %d duplicate DB entries.", dupes_removed)

        # ── Phase 2: Remove stale entries (in DB but not on disk) ───────
        db_paths = await self.api.async_get_local_art_paths()
        stale_count = 0

        def _check_stale():
            """Return list of DB paths whose files no longer exist on disk."""
            stale = []
            for raw_path in db_paths:
                try:
                    path = ensure_allowed_local_path(self.hass, raw_path)
                except UnsafeLocalPathError:
                    stale.append(raw_path)
                    continue
                if not path.is_file():
                    stale.append(raw_path)
            return stale

        stale_paths = await self.hass.async_add_executor_job(_check_stale)

        for path in stale_paths:
            removed = await self.api.async_remove_local_art_by_path(path)
            if removed:
                stale_count += 1
                _LOGGER.info("Sync Library: Removed stale entry (file missing): %s", os.path.basename(path))

        if stale_count > 0:
            _LOGGER.info("Sync Library: Cleaned up %d stale DB entries.", stale_count)
            # Refresh db_paths after cleanup
            db_paths = await self.api.async_get_local_art_paths()

        # ── Phase 3: Add untracked files (on disk but not in DB) ────────
        analyzer, analyzer_err = self._build_analyzer()

        def _get_disk_files():
            library_dir = ensure_allowed_local_path(self.hass, self._library_dir)
            if not library_dir.is_dir():
                return []
            tracked_paths = set()
            for raw_path in db_paths:
                try:
                    tracked_paths.add(
                        str(ensure_allowed_local_path(self.hass, raw_path))
                    )
                except UnsafeLocalPathError:
                    continue
            missing = []
            for entry in library_dir.iterdir():
                if entry.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                try:
                    candidate = ensure_allowed_local_path(self.hass, entry)
                except UnsafeLocalPathError:
                    _LOGGER.warning("Sync Library: Skipping out-of-root image")
                    continue
                if candidate.is_file() and str(candidate) not in tracked_paths:
                    missing.append(candidate)
            return missing

        try:
            missing_files = await self.hass.async_add_executor_job(_get_disk_files)
        except UnsafeLocalPathError as err:
            _LOGGER.error("Sync Library: Rejected library directory: %s", err)
            missing_files = []
        added_count = 0

        if missing_files and analyzer_err:
            _LOGGER.warning(
                "Sync Library: Found %d untracked images but the AI analyzer is unavailable: %s "
                "Stale/duplicate cleanup was completed, but new images cannot be tagged.",
                len(missing_files), analyzer_err
            )
        elif missing_files:
            _LOGGER.info("Sync Library: Found %d untracked images. Starting AI analysis...", len(missing_files))

            for path in missing_files:
                try:
                    def _read_and_probe():
                        trusted_path = ensure_allowed_local_path(self.hass, path)
                        with trusted_path.open("rb") as file_handle:
                            data = file_handle.read()
                        with Image.open(trusted_path) as img:
                            w, h = img.size
                        return data, w, h, len(data)

                    data, width, height, size = await self.hass.async_add_executor_job(_read_and_probe)
                    
                    result = await analyzer.analyze_image(data, prompt="Describe this image")
                    if "error" in result:
                        error_str = str(result['error'])
                        if "429" in error_str:
                            _LOGGER.warning(
                                "Sync Library: Gemini rate limit (429). Stopping. %d images added so far.", added_count
                            )
                            break
                        _LOGGER.warning("Sync Library: AI failed for '%s': %s", os.path.basename(path), error_str)
                        continue

                    tags = ",".join(result.get("tags", []))
                    description = result.get("description", "")

                    await self.api.async_add_local_art(
                        file_path=str(path),
                        tags=tags,
                        description=description,
                        width=width,
                        height=height,
                        file_size=size
                    )
                    added_count += 1
                    _LOGGER.info("Sync Library: Added '%s' -> Tags: %s", os.path.basename(path), tags)
                except Exception as e:
                    _LOGGER.error("Sync Library: Failed to process '%s': %s", os.path.basename(path), e)

        _LOGGER.info(
            "Sync Library: Finished. Added: %d, Stale removed: %d, Duplicates removed: %d",
            added_count, stale_count, dupes_removed
        )
        return {"added": added_count, "stale_removed": stale_count, "duplicates_removed": dupes_removed}
