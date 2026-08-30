# Contributing

Thanks for your interest in improving **Samsung Frame Art Director**!

## Before you start
- Read [`ARCHITECTURE.md`](ARCHITECTURE.md) — it explains the module layout, the
  SQLite data model, the key control flows, and **why** the TV‑API fallback logic
  exists. The Samsung Art Mode API is undocumented and varies by model/firmware,
  so prefer *adding* a guarded path over removing one.
- This is a config‑entry‑only integration (no YAML). It targets **Home Assistant
  2024.7+** (the options flow uses collapsible form sections introduced in that release).

## Dev setup & checks
```bash
# from the repo root
pip install -r requirements_test.lock  # current HA 2026.8 / Python 3.14 stack
ruff check custom_components tests
pytest --cov=custom_components.samsung_frame_art_director --cov-fail-under=68
```

`requirements_test.txt` contains the direct test tools. The generated
`requirements_test.lock` pins the current stable test stack. The parallel
`requirements_test_minimum.lock` pins the declared Home Assistant 2024.7 / Python
3.12 compatibility floor. Regenerate them intentionally with:

```bash
uv pip compile --python-version 3.14.2 requirements_test.txt -o requirements_test.lock
uv pip compile --python-version 3.12 requirements_test_minimum.txt -o requirements_test_minimum.lock
```

CI runs Ruff once and pytest against both compatibility lines, plus **hassfest**
and **HACS** validation. Feature branches run through their pull request only;
direct `main` pushes still run CI.

### Coverage gate

The initial measured floor is **68%** and must not be lowered to make a change
pass. Raise it as behavior tests land: first 75%, then 85%, and finally above
95%, which is the Home Assistant
[Silver test-coverage target](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-coverage/).
Prioritize public Home Assistant seams (actions, config flows, entities, media,
and authenticated views) over tests coupled to private helpers.

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
