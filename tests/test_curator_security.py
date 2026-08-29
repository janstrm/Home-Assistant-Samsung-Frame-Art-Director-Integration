"""Security behavior for curator filesystem boundaries."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.samsung_frame_art_director.curator import ContentCurator


def _curator(hass, *, inbox_dir: Path, library_dir: Path):
    entry = SimpleNamespace(
        options={
            "inbox_dir": str(inbox_dir),
            "library_dir": str(library_dir),
        }
    )
    api = SimpleNamespace(
        async_remove_duplicate_local_art=AsyncMock(return_value=0),
        async_get_local_art_paths=AsyncMock(return_value=[]),
        async_remove_local_art_by_path=AsyncMock(return_value=True),
        async_add_local_art=AsyncMock(),
    )
    curator = ContentCurator(hass, entry, api)
    analyzer = SimpleNamespace(analyze_image=AsyncMock(return_value={"tags": []}))
    curator._build_analyzer = lambda: (analyzer, None)
    return curator, api, analyzer


async def test_process_inbox_rejects_a_prefix_collision(hass):
    """Configured inboxes cannot escape through a similarly named root."""
    config_root = Path(hass.config.path())
    outside = config_root.with_name(f"{config_root.name}-outside-curator")
    library = Path(hass.config.path("www", "library"))
    curator, api, analyzer = _curator(
        hass,
        inbox_dir=outside,
        library_dir=library,
    )

    result = await curator.async_process_inbox()

    assert result["count"] == 0
    assert "error" in result
    analyzer.analyze_image.assert_not_awaited()
    api.async_add_local_art.assert_not_awaited()


async def test_sync_library_skips_a_symlink_escape(hass):
    """A library symlink cannot make the curator analyze an outside file."""
    library = Path(hass.config.path("www", "library"))
    library.mkdir(parents=True, exist_ok=True)
    config_root = Path(hass.config.path())
    outside = config_root.with_name(f"{config_root.name}-outside-curator") / "outside.jpg"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"outside")
    link = library / "escape.jpg"
    try:
        link.symlink_to(outside)
    except OSError as err:
        pytest.skip(f"symlinks unavailable: {err}")
    curator, api, analyzer = _curator(
        hass,
        inbox_dir=Path(hass.config.path("www", "inbox")),
        library_dir=library,
    )

    result = await curator.async_sync_library()

    assert result["added"] == 0
    analyzer.analyze_image.assert_not_awaited()
    api.async_add_local_art.assert_not_awaited()
