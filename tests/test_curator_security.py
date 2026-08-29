"""Security behavior for curator filesystem boundaries."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

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
    curator._build_analyzer = lambda: (analyzer, "test-key", None)
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


async def test_sync_library_registers_an_in_root_file_as_a_canonical_string(hass):
    """The secured success path still passes a SQLite-bindable path to the API."""
    library = Path(hass.config.path("www", "library"))
    library.mkdir(parents=True, exist_ok=True)
    image_path = library / "tracked.jpg"
    Image.new("RGB", (2, 2), (10, 20, 30)).save(image_path, "JPEG")
    curator, api, analyzer = _curator(
        hass,
        inbox_dir=Path(hass.config.path("www", "inbox")),
        library_dir=library,
    )

    result = await curator.async_sync_library()

    assert result["added"] == 1
    analyzer.analyze_image.assert_awaited_once()
    assert api.async_add_local_art.await_args.kwargs["file_path"] == str(
        image_path.resolve()
    )


async def test_process_inbox_rejects_oversized_input_before_ai(hass):
    """Inbox files are byte-bounded before any provider sees their contents."""
    inbox = Path(hass.config.path("www", "oversized-inbox"))
    library = Path(hass.config.path("www", "oversized-library"))
    inbox.mkdir(parents=True, exist_ok=True)
    image_path = inbox / "large.png"
    Image.new("RGB", (2, 2), (10, 20, 30)).save(image_path, "PNG")
    curator, api, analyzer = _curator(
        hass,
        inbox_dir=inbox,
        library_dir=library,
    )

    with patch(
        "custom_components.samsung_frame_art_director.curator.MAX_AI_IMAGE_BYTES",
        32,
    ):
        result = await curator.async_process_inbox()

    assert result == {"count": 0, "skipped": 1}
    analyzer.analyze_image.assert_not_awaited()
    api.async_add_local_art.assert_not_awaited()


async def test_sync_library_rejects_excessive_dimensions_before_ai(hass):
    """Decoded dimensions are bounded before an untracked image reaches AI."""
    library = Path(hass.config.path("www", "dimension-library"))
    library.mkdir(parents=True, exist_ok=True)
    image_path = library / "bomb.png"
    Image.new("RGB", (2, 2), (10, 20, 30)).save(image_path, "PNG")
    curator, api, analyzer = _curator(
        hass,
        inbox_dir=Path(hass.config.path("www", "dimension-inbox")),
        library_dir=library,
    )

    with patch(
        "custom_components.samsung_frame_art_director.curator.MAX_AI_IMAGE_PIXELS",
        3,
    ):
        result = await curator.async_sync_library()

    assert result["added"] == 0
    analyzer.analyze_image.assert_not_awaited()
    api.async_add_local_art.assert_not_awaited()
