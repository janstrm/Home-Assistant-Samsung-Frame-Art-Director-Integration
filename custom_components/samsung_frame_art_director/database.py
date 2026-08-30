"""Prepare entry-owned SQLite databases for Samsung Frame runtimes."""

from __future__ import annotations

import os
import sqlite3

from homeassistant.core import HomeAssistant

from .const import DB_DIR, DB_FILE


def _prepare_entry_database(legacy_path: str, entry_path: str) -> None:
    """Create the database directory and migrate legacy data once."""
    os.makedirs(os.path.dirname(entry_path), exist_ok=True)
    if os.path.exists(entry_path):
        with sqlite3.connect(entry_path) as connection:
            connection.execute("DROP TABLE IF EXISTS local_art")
        return
    if not os.path.exists(legacy_path):
        return

    migration_path = f"{entry_path}.migrating"
    try:
        with (
            sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True) as source,
            sqlite3.connect(migration_path) as destination,
        ):
            source.backup(destination)
            destination.execute("DROP TABLE IF EXISTS local_art")
        os.replace(migration_path, entry_path)
    finally:
        if os.path.exists(migration_path):
            os.remove(migration_path)


async def async_prepare_entry_database(
    hass: HomeAssistant, entry_id: str
) -> tuple[str, str]:
    """Return the isolated TV-state and shared local-art database paths."""
    legacy_path = hass.config.path(f"{DB_DIR}/{DB_FILE}")
    db_stem, db_extension = os.path.splitext(DB_FILE)
    entry_file = f"{db_stem}_{entry_id}{db_extension}"
    entry_path = hass.config.path(f"{DB_DIR}/{entry_file}")
    await hass.async_add_executor_job(
        _prepare_entry_database,
        legacy_path,
        entry_path,
    )
    return entry_path, legacy_path
