"""Curator module for AI-based image processing and organization.

This module handles:
1. Scanning the inbox folder.
2. Analyzing images using the configured AI provider.
3. Saving metadata (tags) to the local database.
4. Moving processed files to the library folder.
"""

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from weakref import WeakKeyDictionary

from PIL import Image

from .ai import (
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MAX_IMAGE_DIMENSION,
    DEFAULT_MAX_IMAGE_PIXELS,
    AIProviderSpec,
    create_analyzer,
    detect_image_mime,
    get_provider_spec,
)
from .api import SamsungFrameClient
from .const import (
    CONF_AI_MODEL,
    CONF_AI_PROVIDER,
    CONF_INBOX_DIR,
    CONF_LIBRARY_DIR,
    DEFAULT_INBOX_DIR,
    DEFAULT_LIBRARY_DIR,
)
from .file_access import UnsafeLocalPathError, ensure_allowed_local_path

_LOGGER = logging.getLogger(__name__)

MAX_AI_IMAGE_BYTES = DEFAULT_MAX_IMAGE_BYTES
MAX_AI_IMAGE_PIXELS = DEFAULT_MAX_IMAGE_PIXELS
MAX_AI_IMAGE_DIMENSION = DEFAULT_MAX_IMAGE_DIMENSION
_CURATOR_LOCKS: WeakKeyDictionary = WeakKeyDictionary()


class UnsafeAIImageError(ValueError):
    """Raised when an image is unsafe to submit to an AI provider."""


@dataclass(frozen=True, slots=True)
class ValidatedAIImage:
    """Validated image payload and metadata passed through the curator."""

    data: bytes
    width: int
    height: int
    file_size: int


def _read_and_validate_ai_image(
    hass,
    path: Path,
    max_bytes: int = MAX_AI_IMAGE_BYTES,
    max_pixels: int = MAX_AI_IMAGE_PIXELS,
    max_dimension: int = MAX_AI_IMAGE_DIMENSION,
) -> ValidatedAIImage:
    """Read and validate an AI input while running in HA's executor."""
    trusted_path = ensure_allowed_local_path(hass, path)
    if trusted_path.stat().st_size > max_bytes:
        raise UnsafeAIImageError("Image exceeds the selected provider's input limit")

    with trusted_path.open("rb") as file_handle:
        data = file_handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnsafeAIImageError("Image exceeds the selected provider's input limit")

    detect_image_mime(data)
    with Image.open(BytesIO(data)) as image:
        width, height = image.size
        if width <= 0 or height <= 0 or width > max_dimension or height > max_dimension or width * height > max_pixels:
            raise UnsafeAIImageError("Image dimensions exceed the AI input limit")
        image.verify()

    return ValidatedAIImage(data, width, height, len(data))


def _curator_lock(hass) -> asyncio.Lock:
    """Return the one curator-operation lock owned by this HA runtime."""
    lock = _CURATOR_LOCKS.get(hass)
    if lock is None:
        lock = asyncio.Lock()
        _CURATOR_LOCKS[hass] = lock
    return lock


def _analyzer_limits(analyzer) -> tuple[int, int, int]:
    """Return provider limits while supporting simple analyzer test adapters."""
    return (
        getattr(analyzer, "max_image_bytes", MAX_AI_IMAGE_BYTES),
        getattr(analyzer, "max_image_pixels", MAX_AI_IMAGE_PIXELS),
        getattr(analyzer, "max_image_dimension", MAX_AI_IMAGE_DIMENSION),
    )


class ContentCurator:
    def __init__(self, hass, entry, api: SamsungFrameClient):
        self.hass = hass
        self.entry = entry
        self.api = api
        self._inbox_dir = entry.options.get(CONF_INBOX_DIR) or DEFAULT_INBOX_DIR
        self._library_dir = entry.options.get(CONF_LIBRARY_DIR) or DEFAULT_LIBRARY_DIR

    def _configured_provider(self) -> tuple[AIProviderSpec | None, str, str]:
        """Return provider spec, matching credential and model atomically."""
        provider = self.entry.options.get(
            CONF_AI_PROVIDER,
            "gemini",
        ).lower()
        spec = get_provider_spec(provider)
        if spec is None:
            return None, "", ""
        api_key = self.entry.options.get(spec.credential_option, "")
        model = self.entry.options.get(spec.model_option, "") or self.entry.options.get(CONF_AI_MODEL, "") or spec.default_model
        return spec, api_key, model

    def _build_analyzer(self):
        """Build the AI analyzer for the configured provider.

        Returns ``(analyzer, api_key, error)`` from one options snapshot.
        """
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        spec, api_key, model = self._configured_provider()
        if spec is None:
            return None, "", "Unsupported AI provider configured."
        if not api_key:
            return (
                None,
                "",
                (f"No {spec.display_name} API key configured. Add it in Settings > Devices > Samsung Frame Art Director > Configure."),
            )
        analyzer, error = create_analyzer(
            spec.key,
            model=model,
            session=async_get_clientsession(self.hass),
        )
        return analyzer, api_key, error

    async def async_process_inbox(self):
        """Serialize and process all images in the inbox."""
        async with _curator_lock(self.hass):
            return await self._async_process_inbox_locked()

    async def _async_process_inbox_locked(self):
        """Process all images in the inbox."""
        analyzer, api_key, analyzer_err = self._build_analyzer()
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
        batch_error = None
        max_bytes, max_pixels, max_dimension = _analyzer_limits(analyzer)

        for source_path in files:
            filename = source_path.name

            # 1. Analyze (Atomic: Stop here if fails)
            try:
                image = await self.hass.async_add_executor_job(
                    _read_and_validate_ai_image,
                    self.hass,
                    source_path,
                    max_bytes,
                    max_pixels,
                    max_dimension,
                )
                result = await analyzer.analyze_image(
                    image.data,
                    prompt="Describe this image",
                    api_key=api_key,
                )

                if "error" in result:
                    error_str = str(result["error"])
                    provider_name = result.get("provider", "AI provider")
                    if result.get("batch_fatal"):
                        _LOGGER.warning(
                            "Process Inbox: %s stopped the batch while processing '%s': %s. "
                            "Stopping. %d images processed so far, %d remaining.",
                            provider_name,
                            filename,
                            error_str,
                            processed_count,
                            len(files) - processed_count - skipped_count - 1,
                        )
                        skipped_count += 1
                        batch_error = error_str
                        break
                    _LOGGER.warning(
                        "Process Inbox: %s analysis failed for '%s': %s. Skipping this image.",
                        provider_name,
                        filename,
                        error_str,
                    )
                    skipped_count += 1
                    continue

                tags = ",".join(result.get("tags", []))
                description = result.get("description", "")

                _LOGGER.info("Process Inbox: AI tagged '%s' -> Tags: %s", filename, tags)

                # 2. Move to Library (Executor)
                def _move(source: Path, original_filename: str):
                    trusted_source = ensure_allowed_local_path(self.hass, source)
                    # Ensure unique filename in library
                    dest_filename = original_filename
                    counter = 1
                    dest_path = ensure_allowed_local_path(self.hass, library_dir / dest_filename)
                    while dest_path.exists():
                        name, ext = os.path.splitext(original_filename)
                        dest_filename = f"{name}_{counter}{ext}"
                        counter += 1
                        dest_path = ensure_allowed_local_path(self.hass, library_dir / dest_filename)

                    shutil.move(str(trusted_source), str(dest_path))
                    return str(dest_path)

                # CRITICAL: We move the file ONLY after AI analysis is successful
                dest_path = await self.hass.async_add_executor_job(
                    _move,
                    source_path,
                    filename,
                )

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
                    width=image.width,
                    height=image.height,
                    file_size=image.file_size,
                )
                processed_count += 1

            except Exception as e:
                _LOGGER.error(
                    "Process Inbox: File moved to '%s' but failed to save metadata to DB: %s. Run 'Sync Library' to recover this image.",
                    dest_path,
                    e,
                )
                skipped_count += 1

        _LOGGER.info("Process Inbox: Finished. Processed: %d, Skipped: %d, Total: %d", processed_count, skipped_count, len(files))
        summary = {"count": processed_count, "skipped": skipped_count}
        if batch_error:
            summary["error"] = batch_error
        return summary

    async def async_sync_library(self):
        """Serialize a full library reconciliation."""
        async with _curator_lock(self.hass):
            return await self._async_sync_library_locked()

    async def _async_sync_library_locked(self):
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
        analyzer, api_key, analyzer_err = self._build_analyzer()

        def _get_disk_files():
            library_dir = ensure_allowed_local_path(self.hass, self._library_dir)
            if not library_dir.is_dir():
                return []
            tracked_paths = set()
            for raw_path in db_paths:
                try:
                    tracked_paths.add(str(ensure_allowed_local_path(self.hass, raw_path)))
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
        skipped_count = 0
        batch_error = None

        if missing_files and analyzer_err:
            _LOGGER.warning(
                "Sync Library: Found %d untracked images but the AI analyzer is unavailable: %s "
                "Stale/duplicate cleanup was completed, but new images cannot be tagged.",
                len(missing_files),
                analyzer_err,
            )
        elif missing_files:
            _LOGGER.info("Sync Library: Found %d untracked images. Starting AI analysis...", len(missing_files))
            max_bytes, max_pixels, max_dimension = _analyzer_limits(analyzer)

            for path in missing_files:
                try:
                    image = await self.hass.async_add_executor_job(
                        _read_and_validate_ai_image,
                        self.hass,
                        path,
                        max_bytes,
                        max_pixels,
                        max_dimension,
                    )

                    result = await analyzer.analyze_image(
                        image.data,
                        prompt="Describe this image",
                        api_key=api_key,
                    )
                    if "error" in result:
                        error_str = str(result["error"])
                        provider_name = result.get("provider", "AI provider")
                        if result.get("batch_fatal"):
                            _LOGGER.warning(
                                "Sync Library: %s stopped the batch: %s. %d images added so far.",
                                provider_name,
                                error_str,
                                added_count,
                            )
                            skipped_count += 1
                            batch_error = error_str
                            break
                        skipped_count += 1
                        _LOGGER.warning(
                            "Sync Library: %s failed for '%s': %s",
                            provider_name,
                            os.path.basename(path),
                            error_str,
                        )
                        continue

                    tags = ",".join(result.get("tags", []))
                    description = result.get("description", "")

                    await self.api.async_add_local_art(
                        file_path=str(path),
                        tags=tags,
                        description=description,
                        width=image.width,
                        height=image.height,
                        file_size=image.file_size,
                    )
                    added_count += 1
                    _LOGGER.info("Sync Library: Added '%s' -> Tags: %s", os.path.basename(path), tags)
                except Exception as e:
                    skipped_count += 1
                    _LOGGER.error("Sync Library: Failed to process '%s': %s", os.path.basename(path), e)

        _LOGGER.info(
            "Sync Library: Finished. Added: %d, Stale removed: %d, Duplicates removed: %d", added_count, stale_count, dupes_removed
        )
        summary = {
            "added": added_count,
            "skipped": skipped_count,
            "stale_removed": stale_count,
            "duplicates_removed": dupes_removed,
        }
        if analyzer_err and missing_files:
            summary["warning"] = analyzer_err
        if batch_error:
            summary["error"] = batch_error
        return summary
