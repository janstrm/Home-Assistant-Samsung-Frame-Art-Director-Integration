# Contributing

Thanks for your interest in improving **Samsung Frame Art Director**!

## Before you start
- Read [`ARCHITECTURE.md`](ARCHITECTURE.md) — it explains the module layout, the
  SQLite data model, the key control flows, and **why** the TV‑API fallback logic
  exists. The Samsung Art Mode API is undocumented and varies by model/firmware,
  so prefer *adding* a guarded path over removing one.
- This is a config‑entry‑only integration (no YAML). It targets **Home Assistant
  2024.6+** (the options flow uses collapsible form sections).

## Dev setup & checks
```bash
# from the repo root
pip install -r requirements_test.txt   # ruff + pytest-homeassistant-custom-component
ruff check custom_components            # lint (pyflakes-level)
pytest                                  # unit tests
```
CI runs the same `ruff` + `pytest`, plus **hassfest** and **HACS** validation on
every push/PR. Please make sure all four are green.

### Conventions
- Keep new code in the style of the surrounding file.
- Bump `manifest.json` `version` for user‑facing changes.
- Manifest keys must be ordered `domain`, `name`, then alphabetical (hassfest).
- Add/adjust a test under `tests/` when you change pure logic (config/options
  flow, migration, helpers, DB, image processing).
- Don't touch the connection/resilience layer in `api.py` without a clear reason
  and, ideally, real‑TV validation.

## Reporting bugs / requesting features
Use the issue templates. For bugs, include your TV model/year and HA version, and
turn on **Verbose debug logging** (integration options → Advanced) before
capturing logs filtered by `samsung_frame_art_director`.

## Maintainer note: brand logo
For the icon/logo to appear in Home Assistant, the integration must be added to
the [home-assistant/brands](https://github.com/home-assistant/brands) repo
(this is a one‑time, separate PR — it can't live in this repo):

1. Prepare a square **`icon.png`** (256×256 or 512×512, transparent background)
   and optionally **`icon@2x.png`**, plus an optional wide **`logo.png`**. The
   existing `custom_components/samsung_frame_art_director/icon.png` can be the
   starting point.
2. Fork `home-assistant/brands`, add the files under
   `custom_integrations/samsung_frame_art_director/`.
3. Open a PR there. Once merged, remove `brands` from the `ignore:` list in
   `.github/workflows/validate.yml` so HACS validates it.
