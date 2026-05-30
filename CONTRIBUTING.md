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

## Brand logo
Since **Home Assistant 2026.3**, custom integrations ship their own brand images
locally — no `home-assistant/brands` PR needed. They live in the integration's
**`brand/`** folder and HA serves them (taking priority over the CDN):

```
custom_components/samsung_frame_art_director/brand/
├── icon.png       # 256×256
└── icon@2x.png    # 512×512
```
Supported filenames also include `dark_icon.png`, `logo.png`/`dark_logo.png` and
their `@2x` variants. The HACS `brands` check still validates the (legacy) brands
repo, so it stays in the `ignore:` list of `.github/workflows/validate.yml`.
