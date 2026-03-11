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
from datetime import datetime
from PIL import Image

from .ai import GeminiAnalyzer
from .api import SamsungFrameClient

_LOGGER = logging.getLogger(__name__)

class ContentCurator:
    def __init__(self, hass, entry, api: SamsungFrameClient):
        self.hass = hass
        self.entry = entry
        self.api = api
        self._inbox_dir = "/media/frame/inbox"
        self._library_dir = "/media/frame/library"

    async def async_process_inbox(self):
        """Process all images in the inbox."""
        api_key = self.entry.options.get("gemini_api_key")
        if not api_key:
            _LOGGER.warning("No Gemini API key configured. Skipping AI processing.")
            return {"count": 0, "error": "No API Key"}

        analyzer = GeminiAnalyzer(api_key)
        
        # Scan inbox (Moved to executor)
        def _list_files():
            if not os.path.exists(self._inbox_dir):
                os.makedirs(self._inbox_dir, exist_ok=True)
            if not os.path.exists(self._library_dir):
                os.makedirs(self._library_dir, exist_ok=True)
            return [
                f for f in os.listdir(self._inbox_dir) 
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
            ]

        try:
            files = await self.hass.async_add_executor_job(_list_files)
        except Exception as e:
             return {"count": 0, "error": f"Inbox scan failed: {e}"}

        if not files:
            _LOGGER.info("Inbox is empty.")
            return {"count": 0}

        _LOGGER.info(f"Found {len(files)} images in inbox. Starting AI analysis...")
        
        processed_count = 0
        
        for filename in files:
            source_path = os.path.join(self._inbox_dir, filename)
            
            # 1. Analyze (Atomic: Stop here if fails)
            try:
                def _read_file():
                    with open(source_path, "rb") as f:
                        return f.read()
                
                data = await self.hass.async_add_executor_job(_read_file)
                result = await analyzer.analyze_image(data, prompt="Describe this image")
                
                if "error" in result:
                    _LOGGER.error(f"AI Error for {filename}: {result['error']}")
                    if "429" in str(result['error']):
                        break
                    continue
                
                tags = ",".join(result.get("tags", []))
                description = result.get("description", "")
                
                _LOGGER.info(f"AI Analysis for {filename}:")
                _LOGGER.info(f"  Tags: {tags}")
                _LOGGER.info(f"  Context: {description}")

                # 2. Probe Metadata (Executor)
                def _probe():
                    with Image.open(source_path) as img:
                        w, h = img.size
                    return w, h, len(data)

                width, height, file_size = await self.hass.async_add_executor_job(_probe)

                # 3. Move to Library (Executor)
                def _move():
                    # Ensure unique filename in library
                    dest_filename = filename
                    counter = 1
                    while os.path.exists(os.path.join(self._library_dir, dest_filename)):
                        name, ext = os.path.splitext(filename)
                        dest_filename = f"{name}_{counter}{ext}"
                        counter += 1
                    
                    dest_path = os.path.join(self._library_dir, dest_filename)
                    shutil.move(source_path, dest_path)
                    return dest_path

                # CRITICAL: We move the file ONLY after AI analysis is successful
                dest_path = await self.hass.async_add_executor_job(_move)

            except Exception as e:
                _LOGGER.error(f"Failed to analyze or prepare {filename}: {e}")
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
                _LOGGER.error(f"Failed to save metadata for {os.path.basename(dest_path)}: {e}")
                # Optional: Move back to inbox if DB fails? 
                # For now, Sync Library will handle it if it's in the folder but not DB.

        return {"count": processed_count}

    async def async_sync_library(self):
        """Scan the library folder for untracked images and add them to the database."""
        api_key = self.entry.options.get("gemini_api_key")
        if not api_key:
             return {"count": 0, "error": "No API Key"}

        analyzer = GeminiAnalyzer(api_key)
        db_paths = await self.api.async_get_local_art_paths()
        
        def _get_missing():
            if not os.path.exists(self._library_dir):
                return []
            return [
                os.path.join(self._library_dir, f)
                for f in os.listdir(self._library_dir)
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
                and os.path.join(self._library_dir, f) not in db_paths
            ]

        missing_files = await self.hass.async_add_executor_job(_get_missing)
        if not missing_files:
            return {"count": 0}

        _LOGGER.info(f"Sync: found {len(missing_files)} untracked images in library. Starting recovery...")
        
        count = 0
        for path in missing_files:
            try:
                def _read_and_probe():
                    with open(path, "rb") as f:
                        data = f.read()
                    with Image.open(path) as img:
                        w, h = img.size
                    return data, w, h, len(data)

                data, width, height, size = await self.hass.async_add_executor_job(_read_and_probe)
                
                result = await analyzer.analyze_image(data, prompt="Describe this image")
                if "error" in result:
                    _LOGGER.error(f"Sync: AI Error for {os.path.basename(path)}: {result['error']}")
                    continue

                tags = ",".join(result.get("tags", []))
                description = result.get("description", "")

                await self.api.async_add_local_art(
                    file_path=path,
                    tags=tags,
                    description=description,
                    width=width,
                    height=height,
                    file_size=size
                )
                count += 1
            except Exception as e:
                _LOGGER.error(f"Sync: Failed to recover {os.path.basename(path)}: {e}")

        return {"count": count}
