"""Tests for async_migrate_entry (config entry v2 -> v3)."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director import async_migrate_entry
from custom_components.samsung_frame_art_director.const import (
    DEFAULT_MATTE_COLOR,
    DEFAULT_MATTE_STYLE,
    DOMAIN,
)


async def test_migrate_v2_to_v3_cleans_legacy_keys(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"host": "1.2.3.4"},
        options={"matte_enabled": True, "slideshow_source_dir": "/media/custom"},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 3
    assert entry.options["matte_style"] == DEFAULT_MATTE_STYLE
    assert entry.options["matte_color"] == DEFAULT_MATTE_COLOR
    assert "matte_enabled" not in entry.options
    assert entry.options["library_dir"] == "/media/custom"
    assert "slideshow_source_dir" not in entry.options


async def test_migrate_legacy_matte_disabled(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, data={"host": "1.2.3.4"}, options={"matte_enabled": False}
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.options["matte_style"] == "none"


async def test_migrate_is_noop_on_v3(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, version=3, data={"host": "1.2.3.4"}, options={"matte_style": "none"}
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 3
    assert entry.options == {"matte_style": "none"}
