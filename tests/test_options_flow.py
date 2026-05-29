"""Tests for the options flow: section flattening and label coverage."""
import json
import pathlib

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director.config_flow import (
    OPTION_SECTIONS,
    OptionsFlowHandler,
)
from custom_components.samsung_frame_art_director.const import DOMAIN

_COMPONENT_DIR = (
    pathlib.Path(__file__).parent.parent
    / "custom_components"
    / "samsung_frame_art_director"
)


async def test_flatten_preserves_entity_managed_keys(hass):
    """Submitting the sectioned form must keep keys managed by entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "1.2.3.4"},
        options={
            "matte_style": "modern",
            "slideshow_enabled": True,
            "gemini_api_key": "OLD",
        },
    )
    entry.add_to_hass(hass)

    handler = OptionsFlowHandler(entry)
    handler.hass = hass

    user_input = {
        "ai_tagging": {"ai_provider": "gemini", "gemini_api_key": "NEW", "openai_api_key": ""},
        "cleanup": {
            "cleanup_max_items": 50,
            "cleanup_max_age_days": 0,
            "cleanup_preserve_current": True,
            "cleanup_only_integration_managed": True,
            "cleanup_dry_run": False,
        },
        "folders": {"inbox_dir": "/i", "library_dir": "/l", "resize_mode": "crop"},
        "power": {"mac_address": "", "use_wol_before_on": False, "use_power_key_on_off": False},
        "advanced": {"ai_model": "", "diagnostics_verbose": False},
    }

    result = await handler.async_step_init(user_input)

    assert result["type"] == "create_entry"
    data = result["data"]
    # Form value applied
    assert data["gemini_api_key"] == "NEW"
    assert data["library_dir"] == "/l"
    # Entity-managed keys preserved (not in the form)
    assert data["matte_style"] == "modern"
    assert data["slideshow_enabled"] is True


def test_every_form_key_has_a_label_in_both_translations():
    for fn in ("strings.json", "translations/en.json"):
        data = json.loads((_COMPONENT_DIR / fn).read_text())
        sections = data["options"]["step"]["init"]["sections"]
        assert set(sections) == set(OPTION_SECTIONS), f"{fn}: section keys mismatch"
        for section_key, option_keys in OPTION_SECTIONS.items():
            labels = set(sections[section_key].get("data", {}))
            for key in option_keys:
                assert key in labels, f"{fn}: missing label for {section_key}.{key}"
