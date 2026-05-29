"""Unit tests for pure helpers in const.py (resolve_matte)."""
from custom_components.samsung_frame_art_director.const import (
    DEFAULT_MATTE_COLOR,
    DEFAULT_MATTE_STYLE,
    MATTE_STYLE_NONE,
    resolve_matte,
)


def test_default_is_none():
    assert resolve_matte({}) == "none"


def test_legacy_enabled_maps_to_default_matte():
    assert resolve_matte({"matte_enabled": True}) == f"{DEFAULT_MATTE_STYLE}_{DEFAULT_MATTE_COLOR}"


def test_legacy_disabled_is_none():
    assert resolve_matte({"matte_enabled": False}) == "none"


def test_style_and_color():
    assert resolve_matte({"matte_style": "modern", "matte_color": "apricot"}) == "modern_apricot"


def test_style_uses_default_color_when_missing():
    assert resolve_matte({"matte_style": "shadowbox"}) == f"shadowbox_{DEFAULT_MATTE_COLOR}"


def test_explicit_none_style_overrides_legacy_flag():
    assert resolve_matte({"matte_enabled": True, "matte_style": MATTE_STYLE_NONE}) == "none"
