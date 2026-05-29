"""Tests for image preprocessing and the local-art DB helpers."""
import io

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
