"""Tests for image preprocessing and the local-art DB helpers."""
import asyncio
import io
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
import pytest

from custom_components.samsung_frame_art_director.api import SamsungFrameClient
from custom_components.samsung_frame_art_director.file_access import media_identifier


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _cleanup_samsungtvws(available_ids, deleted_ids):
    """Return a fake TV boundary that records cleanup deletions."""

    class FakeArt:
        def get_current(self):
            return None

        def available(self):
            return [{"content_id": content_id} for content_id in available_ids]

        def delete_list(self, content_ids):
            deleted_ids.extend(content_ids)

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    return SimpleNamespace(SamsungTVWS=FakeTV)


async def test_preprocess_crop_outputs_target_size(hass):
    client = SamsungFrameClient(hass, "1.2.3.4")
    client.set_resize_mode("crop")
    out = await client.async_preprocess_image(_jpeg(1000, 1500))
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (3840, 2160)


async def test_preprocess_fit_outputs_target_size(hass):
    client = SamsungFrameClient(hass, "1.2.3.4")
    client.set_resize_mode("fit")
    out = await client.async_preprocess_image(_jpeg(1000, 1500))
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (3840, 2160)


async def test_upload_image_returns_tv_content_id(hass):
    """Upload returns the TV ID, keeps the Art token, and closes Art sockets."""
    art_clients = []
    persisted_tokens = []

    class FakeArt:
        def __init__(self):
            self.token = "NEW"
            self.closed = False
            art_clients.append(self)

        def supported(self):
            return True

        def get_artmode(self):
            return "on"

        def get_current(self):
            return {"content_id": "MY-CONTENT-123"}

        def available(self):
            return []

        def upload(self, _image, **_kwargs):
            return "MY-CONTENT-123"

        def select_image(self, _content_id, *, show=True):
            return show

        def change_matte(self, *_args, **_kwargs):
            return True

        def close(self):
            self.closed = True

    class FakeTV:
        token = "OLD"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    client = SamsungFrameClient(hass, "1.2.3.4", token="OLD")
    client.set_token_persister(persisted_tokens.append)

    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        content_id = await client.async_upload_image(_jpeg(100, 100))

    assert content_id == "MY-CONTENT-123"
    assert persisted_tokens == ["NEW"]
    assert art_clients
    assert all(art.closed for art in art_clients)


async def test_upload_selection_timeout_does_not_duplicate_upload(hass):
    """A post-upload selection timeout must not upload the image again."""
    upload_calls = 0

    class FakeArt:
        def supported(self):
            return True

        def get_artmode(self):
            return "on"

        def upload(self, _image, **_kwargs):
            nonlocal upload_calls
            upload_calls += 1
            return f"MY-CONTENT-{upload_calls}"

        def select_image(self, _content_id, *, show=True):
            assert show is True
            time.sleep(0.2)

        def change_matte(self, *_args, **_kwargs):
            return True

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    client = SamsungFrameClient(hass, "1.2.3.4")
    real_wait_for = asyncio.wait_for

    async def short_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.05)

    with (
        patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}),
        patch(
            "custom_components.samsung_frame_art_director.api.asyncio.wait_for",
            side_effect=short_wait_for,
        ),
    ):
        content_id = await client.async_upload_image(_jpeg(100, 100))

    assert content_id == "MY-CONTENT-1"
    assert upload_calls == 1


@pytest.mark.parametrize("matte", ["none", "shadowbox_polar"])
async def test_upload_matte_fallback_does_not_overwrite_portrait_matte(
    hass,
    matte,
):
    """LS03D/F accepts a landscape matte when portrait matte is omitted."""
    matte_calls = []
    applied_mattes = []

    class FakeResponseError(Exception):
        pass

    class FakeArt:
        token = "token"

        def supported(self):
            return True

        def get_artmode(self):
            return "on"

        def upload(self, _image, **_kwargs):
            return "MY-MATTE"

        def select_image(self, _content_id, *, show=True):
            assert show is True

        def change_matte(
            self,
            content_id,
            matte_id=None,
            portrait_matte=None,
        ):
            matte_calls.append((content_id, matte_id, portrait_matte))
            if portrait_matte is not None:
                raise FakeResponseError(
                    "`change_matte` request failed with error number -7"
                )
            applied_mattes.append(matte_id)

        def close(self):
            return None

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")

    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        content_id = await client.async_upload_image(
            _jpeg(100, 100),
            matte=matte,
        )

    assert content_id == "MY-MATTE"
    assert matte_calls == [("MY-MATTE", matte, None)]
    assert applied_mattes == [matte]


async def test_upload_reuses_existing_content_for_the_same_source(hass, tmp_path):
    """An already uploaded source is selected instead of uploaded again."""
    upload_calls = 0
    matte_calls = []
    selected_ids = []

    class FakeArt:
        token = "token"

        def supported(self):
            return True

        def get_artmode(self):
            return "on"

        def get_current(self):
            return {"content_id": "MY-EXISTING"}

        def available(self):
            return [{"content_id": "MY-EXISTING"}]

        def upload(self, _image, **_kwargs):
            nonlocal upload_calls
            upload_calls += 1
            return "MY-NEW"

        def select_image(self, content_id, *, show=True):
            assert show is True
            selected_ids.append(content_id)

        def change_matte(self, content_id, matte_id=None, portrait_matte=None):
            matte_calls.append((content_id, matte_id, portrait_matte))

        def close(self):
            return None

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    source_file = "/media/frame/library/sunrise.jpg"
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art("MY-EXISTING", source_file=source_file)

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        content_id = await client.async_upload_image(
            _jpeg(100, 100),
            matte="shadowbox_polar",
            source_file=source_file,
        )

    assert content_id == "MY-EXISTING"
    assert upload_calls == 0
    assert matte_calls == [("MY-EXISTING", "shadowbox_polar", None)]
    assert selected_ids == ["MY-EXISTING"]


async def test_upload_reuse_propagates_matte_failure_without_upload(hass, tmp_path):
    """A reused image cannot report success when its requested matte failed."""
    upload_calls = 0

    class FakeResponseError(Exception):
        pass

    class FakeArt:
        token = "token"

        def available(self):
            return [{"content_id": "MY-EXISTING"}]

        def upload(self, _image, **_kwargs):
            nonlocal upload_calls
            upload_calls += 1
            return "MY-NEW"

        def select_image(self, _content_id, *, show=True):
            assert show is True

        def change_matte(self, _content_id, matte_id=None):
            raise FakeResponseError(f"matte rejected: {matte_id}")

        def close(self):
            return None

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    source_file = "/media/frame/library/sunrise.jpg"
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art("MY-EXISTING", source_file=source_file)

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    with (
        patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}),
        pytest.raises(FakeResponseError, match="matte rejected"),
    ):
        await client.async_upload_image(
            _jpeg(100, 100),
            matte="shadowbox_polar",
            source_file=source_file,
        )

    assert upload_calls == 0


async def test_upload_does_not_reuse_source_missing_from_target_tv(hass, tmp_path):
    """A source mapping from another TV cannot suppress a required upload."""
    upload_calls = 0
    selected_ids = []

    class FakeArt:
        token = "token"

        def supported(self):
            return True

        def get_artmode(self):
            return "on"

        def get_current(self):
            return {"content_id": "MY-NEW"}

        def available(self):
            return []

        def upload(self, _image, **_kwargs):
            nonlocal upload_calls
            upload_calls += 1
            return "MY-NEW"

        def select_image(self, content_id, *, show=True, **_kwargs):
            assert show is True
            selected_ids.append(content_id)

        def close(self):
            return None

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    source_file = "/media/frame/library/sunrise.jpg"
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art("MY-OTHER-TV", source_file=source_file)

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        content_id = await client.async_upload_image(
            _jpeg(100, 100),
            source_file=source_file,
        )

    assert content_id == "MY-NEW"
    assert upload_calls == 1
    assert selected_ids == ["MY-NEW"]


async def test_upload_checks_all_source_ids_for_the_target_tv(hass, tmp_path):
    """The target-TV list wins over stale global DB state from another TV."""
    upload_calls = 0
    selected_ids = []

    class FakeArt:
        token = "token"

        def available(self):
            return [{"content_id": "MY-THIS-TV"}]

        def upload(self, _image, **_kwargs):
            nonlocal upload_calls
            upload_calls += 1
            return "MY-NEW"

        def select_image(self, content_id, *, show=True, **_kwargs):
            assert show is True
            selected_ids.append(content_id)

        def close(self):
            return None

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    source_file = "/media/frame/library/sunrise.jpg"
    db_path = tmp_path / "art.db"
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(db_path))
    await client.async_track_art("MY-THIS-TV", source_file=source_file)
    await client.async_track_art("MY-OTHER-TV", source_file=source_file)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE art_library SET last_displayed_at = ?, on_tv = 0 "
            "WHERE content_id = ?",
            (1, "MY-THIS-TV"),
        )
        conn.execute(
            "UPDATE art_library SET last_displayed_at = ? WHERE content_id = ?",
            (2, "MY-OTHER-TV"),
        )

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        content_id = await client.async_upload_image(
            _jpeg(100, 100),
            source_file=source_file,
        )

    assert content_id == "MY-THIS-TV"
    assert upload_calls == 0
    assert selected_ids == ["MY-THIS-TV"]


async def test_upload_does_not_duplicate_when_reuse_check_fails(hass, tmp_path):
    """A transient target-TV check failure must not trigger a fresh upload."""
    upload_calls = 0
    client_timeouts = []

    class FakeArt:
        token = "token"

        def available(self):
            raise ConnectionError("target TV unavailable")

        def upload(self, _image, **_kwargs):
            nonlocal upload_calls
            upload_calls += 1
            return "MY-NEW"

        def close(self):
            return None

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **kwargs):
            client_timeouts.append(kwargs.get("timeout"))
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    source_file = "/media/frame/library/sunrise.jpg"
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art("MY-EXISTING", source_file=source_file)

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    with (
        patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}),
        pytest.raises(ConnectionError, match="target TV unavailable"),
    ):
        await client.async_upload_image(
            _jpeg(100, 100),
            source_file=source_file,
        )

    assert upload_calls == 0
    assert client_timeouts == [30]


async def test_upload_does_not_proceed_when_source_lookup_fails(hass, tmp_path):
    """A DB error cannot be mistaken for proof that the source is absent."""
    source_file = "/media/frame/library/sunrise.jpg"
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art("MY-EXISTING", source_file=source_file)

    with (
        patch("sqlite3.connect", side_effect=sqlite3.OperationalError("locked")),
        patch.object(
            client,
            "async_preprocess_image",
            side_effect=AssertionError("upload path reached"),
        ),
        pytest.raises(sqlite3.OperationalError, match="locked"),
    ):
        await client.async_upload_image(
            _jpeg(100, 100),
            source_file=source_file,
        )


async def test_cancelled_reuse_check_does_not_leave_an_art_worker(hass, tmp_path):
    """Cancellation waits for the blocking Art worker before releasing lock."""
    check_started = threading.Event()
    release_check = threading.Event()
    worker_finished = threading.Event()

    class FakeArt:
        token = "token"

        def available(self):
            check_started.set()
            release_check.wait(timeout=1)
            return [{"content_id": "MY-EXISTING"}]

        def select_image(self, _content_id, *, show=True, **_kwargs):
            assert show is True
            worker_finished.set()

        def close(self):
            return None

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    source_file = "/media/frame/library/sunrise.jpg"
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art("MY-EXISTING", source_file=source_file)

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        task = asyncio.create_task(
            client.async_upload_image(
                _jpeg(100, 100),
                source_file=source_file,
            )
        )
        await asyncio.to_thread(check_started.wait, 1)
        assert check_started.is_set()

        task.cancel()
        await asyncio.sleep(0.05)
        worker_is_contained = not task.done()
        release_check.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.to_thread(worker_finished.wait, 1)

    assert worker_is_contained
    assert worker_finished.is_set()


async def test_concurrent_uploads_create_only_one_tv_copy(hass, tmp_path):
    """Concurrent first displays of one source share the first TV upload."""
    upload_calls = 0
    uploaded_ids = []

    class FakeArt:
        token = "token"

        def supported(self):
            return True

        def get_artmode(self):
            return "on"

        def available(self):
            return [{"content_id": content_id} for content_id in uploaded_ids]

        def upload(self, _image, **_kwargs):
            nonlocal upload_calls
            upload_calls += 1
            content_id = f"MY-NEW-{upload_calls}"
            uploaded_ids.append(content_id)
            return content_id

        def select_image(self, _content_id, *, show=True, **_kwargs):
            assert show is True

        def close(self):
            return None

    class FakeTV:
        token = "token"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    source_file = "/media/frame/library/sunrise.jpg"
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        results = await asyncio.gather(
            client.async_upload_image(
                _jpeg(100, 100),
                source_file=source_file,
            ),
            client.async_upload_image(
                _jpeg(100, 100),
                source_file=source_file,
            ),
        )

    assert results == ["MY-NEW-1", "MY-NEW-1"]
    assert upload_calls == 1


async def test_cleanup_never_deletes_manual_tv_art(hass, tmp_path):
    """Automatic cleanup only deletes art with integration provenance."""
    deleted_ids = []

    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art("MY-MANUAL")
    await client.async_track_art(
        "MY-INTEGRATION",
        source_file="/media/frame/library/managed.jpg",
    )

    fake_samsungtvws = _cleanup_samsungtvws(
        ["MY-MANUAL", "MY-INTEGRATION"],
        deleted_ids,
    )
    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        summary = await client.async_cleanup_storage(
            max_items=0,
            only_integration_managed=False,
            preserve_current=False,
        )

    assert summary["deleted"] == ["MY-INTEGRATION"]
    assert deleted_ids == ["MY-INTEGRATION"]


async def test_cleanup_max_items_counts_only_managed_tv_art(hass, tmp_path):
    """Manual TV art must not force managed art below its configured limit."""
    deleted_ids = []
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    for content_id in ("MY-MANAGED-1", "MY-MANAGED-2"):
        await client.async_track_art(
            content_id,
            source_file=f"/media/frame/library/{content_id}.jpg",
        )

    available_ids = ["MY-MANUAL-1", "MY-MANUAL-2", "MY-MANAGED-1", "MY-MANAGED-2"]
    fake_samsungtvws = _cleanup_samsungtvws(available_ids, deleted_ids)
    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        summary = await client.async_cleanup_storage(
            max_items=2,
            preserve_current=False,
        )

    assert summary["to_delete"] == []
    assert summary["deleted"] == []
    assert deleted_ids == []


@pytest.mark.parametrize("dry_run", [False, True])
async def test_cleanup_preserving_unknown_current_art_fails_closed(
    hass, tmp_path, dry_run
):
    """Cleanup must not plan or perform deletion when current art is unknown."""
    deleted_ids = []
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art(
        "MY-MANAGED",
        source_file="/media/frame/library/managed.jpg",
    )

    fake_samsungtvws = _cleanup_samsungtvws(["MY-MANAGED"], deleted_ids)
    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        summary = await client.async_cleanup_storage(
            max_items=0,
            preserve_current=True,
            dry_run=dry_run,
        )

    assert summary["to_delete"] == []
    assert summary["deleted"] == []
    assert summary["errors"] == [
        "Current artwork could not be determined; deletion aborted"
    ]
    assert deleted_ids == []


async def test_cleanup_without_provenance_db_deletes_nothing(hass):
    """Cleanup fails closed when no provenance database is configured."""
    deleted_ids = []
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    fake_samsungtvws = _cleanup_samsungtvws(["MY-MANUAL"], deleted_ids)

    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        summary = await client.async_cleanup_storage(
            max_items=0,
            only_integration_managed=False,
            preserve_current=False,
        )

    assert summary["to_delete"] == []
    assert summary["deleted"] == []
    assert deleted_ids == []


async def test_cleanup_dry_run_excludes_manual_tv_art(hass, tmp_path):
    """The reported dry-run scenario contains no manual deletion candidates."""
    deleted_ids = []
    client = SamsungFrameClient(hass, "1.2.3.4", token="token")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art("MY-MANUAL")
    fake_samsungtvws = _cleanup_samsungtvws(["MY-MANUAL"], deleted_ids)

    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        summary = await client.async_cleanup_storage(
            max_items=0,
            only_integration_managed=False,
            preserve_current=False,
            dry_run=True,
        )

    assert summary["to_delete"] == []
    assert summary["deleted"] == []
    assert deleted_ids == []


async def test_get_state_falls_back_gracefully_without_tv(hass):
    # No TV reachable: the per-call path must degrade to a safe empty result.
    client = SamsungFrameClient(hass, "127.0.0.1")
    assert await client.async_get_state() == {"status": None, "content_id": None}


async def test_get_state_retains_art_token_and_closes_art_socket(hass):
    """State polling keeps a rotated Art token and closes its child socket."""
    art_clients = []
    tv_clients = []
    persisted_tokens = []

    class FakeArt:
        def __init__(self):
            self.token = "OLD"
            self.closed = False
            art_clients.append(self)

        def get_artmode(self):
            self.token = "NEW"
            return "on"

        def get_current(self):
            return {"content_id": "MY-CURRENT"}

        def close(self):
            self.closed = True

    class FakeTV:
        token = "OLD"

        def __init__(self, *_args, **_kwargs):
            self.closed = False
            tv_clients.append(self)

        def art(self):
            return FakeArt()

        def close(self):
            self.closed = True

    fake_samsungtvws = SimpleNamespace(SamsungTVWS=FakeTV)
    client = SamsungFrameClient(hass, "1.2.3.4", token="OLD")
    client.set_token_persister(persisted_tokens.append)

    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        state = await client.async_get_state()

    assert state == {"status": "on", "content_id": "MY-CURRENT"}
    assert persisted_tokens == ["NEW"]
    assert len(art_clients) == 1
    assert art_clients[0].closed is True
    assert tv_clients[0].closed is True


def test_manifest_requires_pypi_samsungtvws():
    # HACS/hassfest discourage git+ requirements. Guard against regressing to a
    # VCS dependency: the requirement must resolve from PyPI.
    import json
    from pathlib import Path

    manifest = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "samsung_frame_art_director"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    reqs = manifest["requirements"]
    assert any(r.startswith("samsungtvws") for r in reqs)
    assert not any("git+" in r or "@git" in r for r in reqs), reqs


async def test_local_art_crud(hass, tmp_path):
    client = SamsungFrameClient(hass, "1.2.3.4")
    client.set_db_path(str(tmp_path / "art.db"))
    local_path = Path(hass.config.path("www", "a.jpg"))
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(_jpeg(10, 10))

    await client.async_add_local_art(
        str(local_path), "tag1,tag2", "desc", 100, 100, local_path.stat().st_size
    )

    paths = await client.async_get_local_art_paths()
    assert str(local_path) in paths

    data = await client.async_get_library_data()
    assert data["items"][0]["id"].startswith("local-")
    assert data["items"][0]["name"] == "a.jpg"
    assert data["items"][0]["content_type"] == "image/jpeg"
    assert "source" not in data["items"][0]
    assert str(local_path.resolve()) not in repr(data)

    assert await client.async_remove_local_art_by_path(str(local_path))
    assert await client.async_get_local_art_paths() == []


async def test_delete_art_accepts_only_an_opaque_tracked_identifier(hass, tmp_path):
    """The delete boundary refuses a raw path even when that file exists."""
    client = SamsungFrameClient(hass, "1.2.3.4")
    client.set_db_path(str(tmp_path / "art.db"))
    local_path = Path(hass.config.path("www", "protected.jpg"))
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(_jpeg(10, 10))
    await client.async_add_local_art(
        str(local_path), "test", "protected", 10, 10, local_path.stat().st_size
    )

    assert await client.async_delete_art(str(local_path)) is False
    assert local_path.exists()
    assert (await client.async_get_library_data())["items"]


async def test_delete_art_removes_an_opaque_tracked_local_item(hass, tmp_path):
    """A database-backed media ID deletes its file and library record together."""
    client = SamsungFrameClient(hass, "1.2.3.4")
    client.set_db_path(str(tmp_path / "art.db"))
    local_path = Path(hass.config.path("www", "delete-me.jpg"))
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(_jpeg(10, 10))
    await client.async_add_local_art(
        str(local_path), "test", "delete", 10, 10, local_path.stat().st_size
    )
    media_id = (await client.async_get_library_data())["items"][0]["id"]

    assert await client.async_delete_art(media_id) is True
    assert not local_path.exists()
    assert (await client.async_get_library_data())["items"] == []


async def test_delete_art_keeps_database_record_when_disk_delete_fails(
    hass,
    tmp_path,
):
    """A failed file deletion remains visible so the user can retry it."""
    client = SamsungFrameClient(hass, "1.2.3.4")
    client.set_db_path(str(tmp_path / "art.db"))
    local_path = Path(hass.config.path("www", "busy.jpg"))
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(_jpeg(10, 10))
    await client.async_add_local_art(
        str(local_path), "test", "busy", 10, 10, local_path.stat().st_size
    )
    media_id = (await client.async_get_library_data())["items"][0]["id"]

    with patch("pathlib.Path.unlink", side_effect=PermissionError("busy")):
        assert await client.async_delete_art(media_id) is False

    assert (await client.async_get_library_data())["items"][0]["id"] == media_id


async def test_delete_art_refuses_a_tracked_out_of_root_file(hass, tmp_path):
    """Even a database row cannot authorize deletion outside trusted HA roots."""
    client = SamsungFrameClient(hass, "1.2.3.4")
    client.set_db_path(str(tmp_path / "art.db"))
    config_root = Path(hass.config.path())
    outside = config_root.with_name(f"{config_root.name}-outside") / "keep.jpg"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(_jpeg(10, 10))
    await client.async_add_local_art(
        str(outside), "test", "outside", 10, 10, outside.stat().st_size
    )

    assert await client.async_delete_art(media_identifier(outside)) is False
    assert outside.exists()
    assert str(outside) in await client.async_get_local_art_paths()


async def test_folder_rotation_rejects_a_prefix_collision(hass):
    """A similarly named directory cannot pass the folder rotation boundary."""
    client = SamsungFrameClient(hass, "1.2.3.4")
    config_root = Path(hass.config.path())
    outside = config_root.with_name(f"{config_root.name}-outside-rotation")
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "art.jpg").write_bytes(_jpeg(10, 10))
    client.async_upload_image = AsyncMock()

    assert await client.async_rotate_from_folder(str(outside)) is False
    client.async_upload_image.assert_not_awaited()
