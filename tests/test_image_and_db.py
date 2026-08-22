"""Tests for image preprocessing and the local-art DB helpers."""
import io
import sys
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from custom_components.samsung_frame_art_director.api import SamsungFrameClient


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


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
    """Callers receive the exact content ID returned by the TV library."""

    class FakeArt:
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

    with patch.dict(sys.modules, {"samsungtvws": fake_samsungtvws}):
        content_id = await client.async_upload_image(_jpeg(100, 100))

    assert content_id == "MY-CONTENT-123"


async def test_get_state_falls_back_gracefully_without_tv(hass):
    # No TV reachable: the per-call path must degrade to a safe empty result.
    client = SamsungFrameClient(hass, "127.0.0.1")
    assert await client.async_get_state() == {"status": None, "content_id": None}


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

    await client.async_add_local_art("/x/a.jpg", "tag1,tag2", "desc", 100, 100, 10)

    paths = await client.async_get_local_art_paths()
    assert "/x/a.jpg" in paths

    data = await client.async_get_library_data()
    assert any(item["id"] == "/x/a.jpg" for item in data["items"])

    assert await client.async_remove_local_art_by_path("/x/a.jpg")
    assert await client.async_get_local_art_paths() == []
