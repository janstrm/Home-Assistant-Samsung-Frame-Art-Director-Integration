"""Local import and explicit later tagging using the real library database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from custom_components.samsung_frame_art_director import async_setup
from custom_components.samsung_frame_art_director.api import SamsungFrameClient
from custom_components.samsung_frame_art_director.curator import ContentCurator


async def test_tagging_action_uses_selected_entry_and_reports_result(hass):
    hass.http = MagicMock()
    await async_setup(hass, {})
    target = SimpleNamespace(entry=object(), runtime=SimpleNamespace(client=object()))
    with (
        patch("custom_components.samsung_frame_art_director.async_resolve_action_targets",
              new_callable=AsyncMock, return_value=[target]),
        patch("custom_components.samsung_frame_art_director.curator.ContentCurator") as curator,
        patch("homeassistant.components.persistent_notification.async_create") as notify,
    ):
        curator.return_value.async_tag_untagged = AsyncMock(return_value={"tagged": 2, "skipped": 0})
        await hass.services.async_call("samsung_frame_art_director", "tag_untagged", {}, blocking=True)
        curator.assert_called_once_with(hass, target.entry, target.runtime.client)
        assert "2 tagged" in notify.call_args.args[1]


async def prepare(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    inbox = tmp_path / "inbox"
    library = tmp_path / "library"
    inbox.mkdir(parents=True)
    library.mkdir()
    api = SamsungFrameClient(hass, "frame.local")
    api.set_db_path(str(tmp_path / "tv.db"))
    api.set_local_db_path(str(tmp_path / "local.db"))
    await api.async_initialize_database()
    entry = SimpleNamespace(options={"ai_provider": "none", "inbox_dir": str(inbox), "library_dir": str(library)})
    return ContentCurator(hass, entry, api), api, inbox, library


@pytest.mark.parametrize("source", ["inbox", "library"])
async def test_local_import_then_tag_preserves_favorites_and_existing_tags(hass, tmp_path, source):
    curator, api, inbox, library = await prepare(hass, tmp_path)
    image = (inbox if source == "inbox" else library) / "art.png"
    Image.new("RGB", (4, 3)).save(image)
    with patch("custom_components.samsung_frame_art_director.curator.create_analyzer") as create:
        result = await (curator.async_process_inbox() if source == "inbox" else curator.async_sync_library())
        create.assert_not_called()
    assert result.get("count", result.get("added")) == 1
    path = str((library / "art.png").resolve())
    assert await api.async_get_untagged_local_art_paths() == [path]
    assert (library / "art.png").exists()
    assert not (inbox / "art.png").exists()
    with patch.object(api, "async_upload_image", new_callable=AsyncMock) as upload:
        assert await api.async_rotate_art()
        upload.assert_awaited_once()
        assert not await api.async_rotate_art(tags=["nature"])
    with api._get_db() as conn:
        conn.execute("UPDATE local_art SET is_favorite=1 WHERE file_path=?", (path,))
        before = dict(conn.execute("SELECT * FROM local_art").fetchone())

    # A normal sync must not classify registered images, even with AI enabled.
    analyzer = SimpleNamespace(analyze_image=AsyncMock(return_value={"tags": ["nature"], "description": "Trees"}))
    curator._build_analyzer = lambda: (analyzer, "key", None)
    await curator.async_sync_library()
    analyzer.analyze_image.assert_not_awaited()
    assert await curator.async_tag_untagged() == {"tagged": 1, "skipped": 0}
    assert await curator.async_tag_untagged() == {"tagged": 0, "skipped": 0}
    analyzer.analyze_image.assert_awaited_once()
    with api._get_db() as conn:
        after = dict(conn.execute("SELECT * FROM local_art").fetchone())
    assert after == {**before, "tags": "nature", "description": "Trees"}
    assert not await api.async_tag_local_art(path, "replacement", "changed")


@pytest.mark.parametrize("provider", ["none", "gemini", "openai", "anthropic", "unknown"])
async def test_tagging_without_configured_analyzer_preserves_library(hass, tmp_path, provider):
    curator, api, inbox, library = await prepare(hass, tmp_path)
    Image.new("RGB", (2, 2)).save(inbox / "art.png")
    await curator.async_process_inbox()
    curator.entry.options["ai_provider"] = provider
    result = await curator.async_tag_untagged()
    assert result["tagged"] == 0
    assert result["error"]
    assert await api.async_get_untagged_local_art_paths() == [str(library / "art.png")]
    if provider != "none":
        Image.new("RGB", (2, 2)).save(inbox / "next.png")
        assert "error" in await curator.async_process_inbox()
        assert (inbox / "next.png").exists()


async def test_later_tagging_failure_keeps_files_and_allows_retry(hass, tmp_path):
    curator, api, inbox, library = await prepare(hass, tmp_path)
    Image.new("RGB", (2, 2)).save(inbox / "art.png")
    await curator.async_process_inbox()
    analyzer = SimpleNamespace(analyze_image=AsyncMock(return_value={"error": "Rate limited", "batch_fatal": True}))
    curator._build_analyzer = lambda: (analyzer, "key", None)
    assert await curator.async_tag_untagged() == {"tagged": 0, "skipped": 1, "error": "Rate limited"}
    assert (library / "art.png").exists()
    assert len(await api.async_get_untagged_local_art_paths()) == 1
    analyzer.analyze_image.return_value = {"tags": ["blue"]}
    assert (await curator.async_tag_untagged())["tagged"] == 1


async def test_local_mode_validates_files_and_later_tagging_rejects_escaped_paths(hass, tmp_path):
    curator, api, inbox, library = await prepare(hass, tmp_path / "config")
    (inbox / "broken.png").write_bytes(b"not an image")
    assert await curator.async_process_inbox() == {"count": 0, "skipped": 1}
    assert (inbox / "broken.png").exists()
    outside = tmp_path / "outside.png"
    Image.new("RGB", (2, 2)).save(outside)
    await api.async_add_local_art(str(outside), "", "", 2, 2, outside.stat().st_size)
    analyzer = SimpleNamespace(analyze_image=AsyncMock())
    curator._build_analyzer = lambda: (analyzer, "key", None)
    assert await curator.async_tag_untagged() == {"tagged": 0, "skipped": 1}
    analyzer.analyze_image.assert_not_awaited()
