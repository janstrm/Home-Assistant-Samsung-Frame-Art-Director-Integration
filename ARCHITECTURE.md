# Architecture & Technical Reference

> **Audience:** contributors and AI coding agents working on this integration.
> For installation and end-user usage, see the [README](README.md).

This document explains *how* the Samsung Frame Art Director integration is put
together and — just as importantly — *why* it is built the way it is. A large
fraction of the code exists to survive Samsung's undocumented, model-dependent,
and frequently-flaky internal WebSocket API. Before "simplifying" any of the
fallback logic, read [The resilience layer](#the-resilience-layer).

---

## 1. What this is

A [Home Assistant](https://www.home-assistant.io/) **custom integration**
(domain: `samsung_frame_art_director`) that manages **Art Mode** on a Samsung
The Frame TV. It is installable via HACS as a custom repository.

It communicates with the TV exclusively over Samsung's **internal WebSocket
API**, via the [`samsungtvws`](https://github.com/NickWaterton/samsung-tv-ws-api)
library (the NickWaterton fork, pinned in `manifest.json`). There is no official
Samsung API; everything here is reverse-engineered and confirmed working on the
**Q65LS03DAU**. Other models/years may behave differently.

Core capabilities:

- Toggle Art Mode on/off **with state verification**.
- Upload local images (auto center-cropped/resized to 3840×2160).
- Maintain a **local SQLite library** of art with AI-generated tags.
- **Rotate** displayed art on a schedule, filtered by tags / favorites / folder.
- **Clean up** the TV's limited internal storage.
- Expose a **gallery sensor** + dashboard for browsing/managing art.

---

## 2. High-level architecture

```
┌──────────────────────── Home Assistant ────────────────────────┐
│                                                                 │
│   Config / Options flow ──► ConfigEntry (host, token, options)  │
│            │                                                    │
│            ▼                                                    │
│   __init__.py  (setup, services, slideshow timer, WS API)       │
│      │             │                │             │             │
│      ▼             ▼                ▼             ▼             │
│  Entities      Services        Slideshow      Curator           │
│ (media_player, (set_artmode,    timer         (process_inbox,   │
│  image, switch, upload_art,    (_run_         sync_library)     │
│  select, number, rotate_*,      slideshow_         │            │
│  text, sensor)  cleanup, ...)   job)               ▼            │
│      │             │                │         ai.py             │
│      └─────────────┴────────────────┘      (Gemini / OpenAI)    │
│                    │                               │            │
│                    ▼                               ▼            │
│         api.py: SamsungFrameClient        Google Gemini /       │
│         (async facade + SQLite DB)        OpenAI REST APIs      │
│                    │                                            │
└────────────────────┼────────────────────────────────────────────┘
                     │  samsungtvws (sync + async)
                     ▼
        Samsung Frame TV  (WebSocket :8002/:8001, encrypted :8000)
                     ▲
   /media/frame/inbox │ /media/frame/library  (HA filesystem)
```

Two external boundaries dominate the design:

1. **The TV** — reached through `samsungtvws`. Connections are short-lived,
   created per-operation, and wrapped in timeouts/retries. The library is
   partly synchronous, so blocking calls are pushed to threads.
2. **The AI provider** — Gemini (default) or OpenAI, reached over HTTPS for
   image → tag analysis. Optional; only used by the curator.

---

## 3. Repository layout

```
custom_components/samsung_frame_art_director/
├── __init__.py        # Setup, teardown, service & WS-API registration, slideshow timer
├── api.py             # SamsungFrameClient: the core TV facade + SQLite library DB (~1.8k lines)
├── bridge.py          # Pairing/handshake + port/method selection (used by config_flow)
├── config_flow.py     # Initial pairing UI + reauth + options flow
├── curator.py         # ContentCurator: inbox processing & library sync (AI tagging)
├── ai.py              # ImageAnalyzer ABC, GeminiAnalyzer, OpenAIAnalyzer, create_analyzer()
├── const.py           # Constants, option keys, defaults
├── views.py           # HTTP view serving local thumbnails to the dashboard
├── sensor.py          # Gallery library sensor (+ gallery page number)
├── image.py           # Live "current artwork" preview entity
├── media_player.py    # Main control entity (power/art-mode status)
├── switch.py          # slideshow_enabled, gallery_favorites_only
├── select.py          # slideshow source + interval pickers; matte style + color
├── number.py          # custom slideshow interval, gallery page
├── text.py            # free-text slideshow/tag filter
├── services.yaml      # Service schemas (UI metadata)
├── strings.json       # config/options flow strings
├── translations/en.json
├── manifest.json      # domain, version, requirements (samsungtvws fork)
└── icon.png
examples/dashboard.yaml # Reference 3-column gallery dashboard
docs/ARCHITECTURE.md    # (this file)
```

---

## 4. Module reference

### `api.py` — `SamsungFrameClient`

The heart of the integration. A single instance per config entry, stored in
`hass.data[DOMAIN][entry_id]["client"]`. It is an **async facade**: every public
method is `async`, and any blocking `samsungtvws` call is run via
`asyncio.to_thread` / `hass.async_add_executor_job`.

Responsibilities:

- **Connection & pairing** — `async_connect_and_pair()` (token capture, DUID).
- **Art Mode** — `async_set_artmode()`, `async_get_artmode_status()`.
- **Upload** — `async_preprocess_image()` (Pillow resize/crop), `async_upload_image()`.
- **Rotation** — `async_rotate_art()` (DB-driven, tag/favorite filtered),
  `async_rotate_from_folder()`, `async_rotate_art_now()` (older mode-based variant),
  `_async_select_image_id()`.
- **Preview** — `async_get_current_art()` (current content id + thumbnail bytes,
  5-second cache).
- **Library DB** — schema init/migration (`_ensure_db()`), tracking, favorites,
  delete, dedup, stale-cleanup, purge, and the gallery data query.
- **TV storage cleanup** — `async_cleanup_storage()`.
- **Diagnostics** — `async_art_diagnostics()`.

A single `asyncio.Lock` (`self._art_lock`) serializes all "art channel"
operations (set-artmode, upload, select, cleanup) so concurrent calls don't
collide on the TV's single WebSocket art channel.

### `bridge.py`

Stateless pairing helpers used **only by the config flow**:
`async_probe_device_info()` (port detection), `async_try_connect()` (token
handshake, async-then-sync), and the encrypted-pairing pair
(`async_encrypted_start_pairing` / `async_encrypted_try_pin`) for legacy H/J
models. Returns `PairResult` objects with `RESULT_*` semantics from `const.py`.

### `config_flow.py`

- **`SamsungFrameConfigFlow`** — probes the host, picks a port, sets a unique id
  from the DUID, and runs either the standard pairing step or the encrypted PIN
  step (selected by model-name prefix `H`/`J`).
- **`OptionsFlowHandler`** — all runtime tunables: AI provider + API keys,
  slideshow, matte, Wake-on-LAN, cleanup thresholds, verbose logging. Option
  changes are hot-applied (slideshow timer reload) **without** a full entry
  reload, to avoid an "unavailable" blip on the entities.

### `curator.py` — `ContentCurator`

Owns the `/media/frame/inbox` → `/media/frame/library` pipeline. Built fresh per
service call. See [§6 flows](#process-inbox).

### `ai.py`

Vision tagging. `ImageAnalyzer` is the ABC; `GeminiAnalyzer` (REST, default) and
`OpenAIAnalyzer` are implementations. `create_analyzer()` is the **single wiring
point** between options and implementations — see [§8](#8-ai-tagging-layer).

### Entity platforms

All entities are thin and read from / write to either the `ConfigEntry.options`
or the `SamsungFrameClient`. Notable:

- `media_player.py` — primary entity; exposes `art_mode_status` attribute.
- `image.py` — serves `async_get_current_art()` bytes as a camera-style preview.
- `sensor.py` — `..._art_library`: state = item count, `items` attribute = full
  gallery list for the dashboard; also defines the gallery-page number helper.

---

## 5. Data model (SQLite)

The DB lives at `<config>/samsung_frame_director/art_library.db`
(`DB_DIR`/`DB_FILE` in `const.py`). It is opened **per operation** (no long-lived
connection) and initialized lazily via `_ensure_db()`.

There are **two tables**, and understanding the split is key:

### `local_art` — source of truth for the library

Rows represent **image files on the HA filesystem** (in `/media/frame/library`),
tagged by AI. This is what the gallery sensor, dashboard, and rotation primarily
read from.

| column | meaning |
|---|---|
| `file_path` (PK) | absolute path on the HA filesystem |
| `tags` | comma-separated AI tags |
| `description` | raw AI description text |
| `processed_at` | ISO timestamp |
| `width`, `height`, `file_size` | probed metadata |
| `is_favorite` | 0/1 (added by migration) |

### `art_library` — what's tracked *on the TV*

Rows represent **content uploaded to / present on the TV** (Samsung content ids
like `MY-C0002_…`). Used for rotation of already-uploaded art and for storage
cleanup (`on_tv`, favorites, age).

The columns the code actually reads/writes are: `content_id` (PK), `tags`,
`source_file` (the local file it came from, enabling instant high-res preview),
`is_favorite`, `created_at`, `last_displayed_at`, `on_tv`, `deleted_at`,
`category`, plus `width`/`height`.

> 🛠 **Maintaining `_ensure_db()`.** The `CREATE TABLE art_library` statement
> declares exactly the column set above, and a guarded `ALTER TABLE ADD COLUMN`
> migration exists for each non-PK column so older databases are upgraded in
> place (idempotently). **If you add a column the code uses, add it in *both*
> places** — the `CREATE TABLE` (for fresh installs) and an
> `if "<col>" not in existing_cols` migration (for existing installs).
> Databases created by a much older schema (`date_added`/`last_seen`/`source`)
> keep those now-unused columns as harmless leftovers; the migrations fill in
> everything the current code needs.

### Why two tables?

`local_art` = "what I have on disk and could show". `art_library` = "what is
currently/previously on the TV's limited internal storage". Rotation can either
re-select something already on the TV (`art_library`, fast) or upload a local
file (`local_art`, slower). Cleanup operates on `art_library` to free TV space
while preserving favorites and the currently-displayed image.

---

## 6. Key control flows

### Pairing (config flow)

1. `async_probe_device_info()` tries port **8002 (SSL)** then **8001**; the
   first that returns device info wins.
2. If the model name starts with `H`/`J` → **encrypted** PIN pairing on port
   8000. Otherwise → standard token pairing.
3. `async_try_connect()` opens a connection (async remote first, sync fallback),
   provokes the on-TV "Allow" prompt, and polls up to ~10 attempts for the user
   to accept. On success a **token** is captured (from the remote object or the
   `token_file`) and stored in the `ConfigEntry`.
4. On every setup, `async_connect_and_pair()` re-validates the token. A
   `PairingTimeoutError` (no token/duid established) raises
   `ConfigEntryAuthFailed`, which triggers the **reauth flow**
   (`async_step_reauth` → `async_step_reauth_confirm`) so the user can
   re-accept on the TV; the new token replaces the old one via
   `async_update_reload_and_abort`. Other (transient/connectivity) failures
   raise `ConfigEntryNotReady` so HA retries.

User-entered hosts are cleaned by `_normalize_host()` (trims whitespace,
strips a `scheme://`, path, and trailing `:port`) before probing.

### Set Art Mode (with verification)

`async_set_artmode(enabled)` → `_async_set_artmode_locked()` (under `_art_lock`):

1. **Early-exit** if already in the desired state.
2. Prefer the **async remote** path (`SamsungTVWSAsyncRemote.art().set_artmode`),
   then verify `get_artmode()` up to 3× with 2s spacing.
3. **Fallback** to the sync client in a thread. On *enable*, if verification
   fails, it force-`select_image()`s a candidate to coax Art Mode on.
4. Optional service-layer extras (in `__init__.py`): **Wake-on-LAN** before ON,
   and a **POWER key** fallback for OFF.

### Upload an image

`async_upload_image(bytes, matte, source_file)`:

1. `async_preprocess_image()` — Pillow: scale-to-fill + center-crop to
   **3840×2160**, JPEG q85.
2. Under `_art_lock`: try the **async art API** twice (ports 8002→8001), each
   attempt uploading + selecting + applying matte. Track the new `content_id` in
   `art_library` with its `source_file`.
3. If async fails, fall back to the **sync** path with up to 5 retries and
   exponential backoff, priming the art channel before each attempt and
   recreating the client on `ConnectionFailure`.

### Process Inbox {#process-inbox}

`ContentCurator.async_process_inbox()`:

1. Build the analyzer via `_build_analyzer()`; bail with a notification if no
   key/provider is configured.
2. List images in `/media/frame/inbox`.
3. For each: **analyze first** (Gemini/OpenAI). On HTTP **429** stop early
   (rate limit); on other errors skip the file.
4. Only after a successful analysis: probe dimensions, **move** the file to
   `/media/frame/library` (unique-name collision handling), then write the row
   into `local_art`. This ordering guarantees a file is never moved without a
   successful tag, and never lost if the DB write fails (recoverable via Sync).

### Sync Library

`ContentCurator.async_sync_library()` — full bidirectional reconciliation:

1. **Dedup** `local_art` (keep newest rowid per `file_path`).
2. **Remove stale** rows whose files no longer exist on disk.
3. **Add untracked** files present on disk but absent from the DB (AI-tagged).
   Phases 1–2 run even without an API key; phase 3 needs the analyzer.

### Rotation / Slideshow

- Timer: `_reload_slideshow_timer()` registers `async_track_time_interval`
  when slideshow is enabled and interval > 0.
- `_run_slideshow_job()` **skips unless the TV is in Art Mode** (don't interrupt
  a movie or wake a powered-off TV), then honors live **dashboard filters**
  (favorites switch, free-text tag filter incl. `-negative` tags) before falling
  back to the configured source type (folder / tags / library).
- `async_rotate_art()` gathers candidates from both tables, fuzzy-matches tags
  (substring, any/all), optionally restricts to favorites, then picks a random
  winner. For TV items it re-selects; for local items it uploads. It **retries**
  on stale local entries (file deleted out from under the DB).

### Cleanup TV storage

`async_cleanup_storage()` reads the TV's current + available content, optionally
restricts to integration-managed ids, **preserves favorites and the current
image**, applies optional age and `max_items` limits (deleting oldest first),
then deletes via `delete_list` (fallback: per-id `delete`) and reconciles the
`on_tv` flags in the DB. Supports `dry_run`.

---

## 7. The resilience layer

> If you remember one thing from this document: the apparent over-engineering in
> `api.py` is deliberate. Samsung's API is inconsistent across firmware/models,
> and the same logical call can fail in different ways. Removing a fallback will
> "work on my TV" and break on someone else's.

Patterns you will see repeated, and why they exist:

- **Stable connection identity (avoids recurring pairing popups).** The Frame
  ties authorization to the `(client name, token)` pair. The integration opens a
  short-lived connection per operation, so every one of them must present the
  same name and token or the TV treats it as a *new device* and re-shows the
  "Allow access" dialog. All sync clients are therefore built through
  `SamsungFrameClient._make_tv()` (always passes `name` + `token`), and
  `_capture_token()` runs on close to **persist any token the TV re-issues** (via
  a loop-safe `set_token_persister` callback wired in `__init__.py`) so
  authorization doesn't drift. Never construct a bare `SamsungTVWS(host)`.
- **Async-first, sync-fallback.** `SamsungTVWSAsyncRemote` / `SamsungTVAsyncArt`
  are preferred (non-blocking, fewer stalls), but not present/working on every
  library version or model — so a synchronous `SamsungTVWS`-in-a-thread path
  always backs it up.
- **Dual ports 8002 → 8001.** SSL is preferred; some units only answer on the
  non-SSL port. Probing and uploads try both.
- **Retries + exponential backoff.** Upload retries 5× on transient
  `ConnectionFailure`, recreating the client between attempts; the art channel
  is "primed" (`supported()` / `get_artmode()`) before attempts.
- **State verification loops.** `set_artmode` doesn't trust the call; it polls
  `get_artmode()` and force-selects an image to coax the mode on.
- **`matte` quirks.** A matte id is `"{style}_{color}"` (e.g. `shadowbox_polar`)
  or `"none"`, resolved from options by `resolve_matte()` in `const.py`. Recent
  `samsungtvws` dropped the `matte` kwarg from `select_image`, so matte is applied
  via `upload(matte=…)` and `change_matte`; `change_matte` wants the literal
  string `"none"` to clear. The `select_image(matte=…)` call is kept only as a
  guarded fast path that falls back on `TypeError`.
- **Multiple thumbnail methods.** `get_thumbnail` → `get_preview` → `get_photo`,
  because availability varies; a local `source_file` is preferred for instant
  high-res previews.
- **`samsungtvws.helper.is_true` monkeypatch** (in `__init__.py`) — patches a
  function missing in some library builds.
- **Broad `except` with debug logging.** Many TV calls raise spurious errors
  (e.g. the `clientConnect` handshake event) even when the action succeeded, so
  failures are logged at debug and the flow continues.

When changing this layer, prefer **adding** a guarded path over removing one,
and keep the debug logging — it is the only diagnostic tool users have.

---

## 8. AI tagging layer

`ai.py` defines:

- `ImageAnalyzer` (ABC) — `analyze_image(bytes, prompt) -> dict` returning
  `{tags, description, provider, model, duration}` or `{error}`.
- `GeminiAnalyzer` — Google Gemini via REST (`aiohttp`), default model
  `gemini-2.0-flash`. Prompts for ~15 keywords including weather/lighting/mood.
- `OpenAIAnalyzer` — GPT-4o vision via the `openai` SDK. **Optional dependency:**
  `openai` is *not* in `manifest.json` requirements, so selecting OpenAI requires
  the package to be installed; the analyzer degrades gracefully (returns an
  error dict) if it's missing.
- `create_analyzer(provider, gemini_api_key, openai_api_key)` — the **factory**
  and the only place that maps the `ai_provider` option to a concrete class.
  Returns `(analyzer, error)`.

The curator never instantiates a concrete analyzer directly; it calls
`self._build_analyzer()` → `create_analyzer()`. **To add a provider:** implement
an `ImageAnalyzer` subclass, add a branch in `create_analyzer()`, add the
provider constant in `const.py`, and add its option(s) to the options flow in
`config_flow.py` (+ `strings.json` / `translations/en.json` labels).

---

## 9. Services, entities & the dashboard

Service schemas live in `services.yaml`; handlers are registered in
`__init__.py`. Domain-targeted services (`set_artmode`, `upload_art`,
`rotate_art_now`, `cleanup_storage`, `art_diagnostics`) resolve their target
`SamsungFrameClient`(s) via `_resolve_clients()` from the entity target;
library/gallery services (`process_inbox`, `sync_library`, `purge_database`,
`toggle_favorite`, `delete_art`, `rotate_favorites`) act on the entry's client.

A **WebSocket command** `samsung_frame_art_director/get_library` and the
`SamsungFrameThumbnailView` HTTP view feed the example gallery dashboard. The
gallery is also exposed via the `..._art_library` sensor's `items` attribute for
template/auto-entities use. The full user-facing service/entity catalog is in
the [README](README.md#-services).

---

## 10. Concurrency & threading model

- The integration is **async**; HA's event loop must never be blocked.
- All `samsungtvws` and filesystem/Pillow calls run off-loop via
  `asyncio.to_thread` or `hass.async_add_executor_job`.
- TV art-channel operations are serialized with `SamsungFrameClient._art_lock`.
- SQLite is accessed with short-lived per-call connections inside executor jobs
  (`_get_db()` / `sqlite3.connect`), avoiding cross-thread connection sharing.
- Network calls are wrapped in `asyncio.wait_for` timeouts (typically 10–120s).

---

## 11. Known quirks & gotchas

- **`art_library` schema is two-place** — `CREATE TABLE` *and* the `ALTER`
  migrations must stay in sync (see [§5](#5-data-model-sqlite)). Adding a column
  in only one place silently breaks either fresh installs or upgrades.
- **`OpenAIAnalyzer` needs a manual dependency.** `openai` isn't declared in
  `manifest.json`. This is intentional (don't force the dep on Gemini users) but
  means OpenAI silently errors until the package is installed.
- **`async_rotate_art_now()` legacy modes.** Its `library` and `aware` modes are
  unimplemented no-ops; the live rotation path is `async_rotate_art()` /
  `async_rotate_from_folder()`. Don't confuse the two.
- **Hardcoded media paths.** `/media/frame/inbox` and `/media/frame/library` are
  hardcoded in `curator.py`; the `slideshow_source_path` option is only a
  fallback for folder rotation.
- **Verbose logging on by default.** `diagnostics_verbose` defaults to `True`
  and bumps several loggers to DEBUG; this is deliberate for field debugging.
- **Per-model variance.** Anything in [§7](#7-the-resilience-layer) may behave
  differently on non-Q-series / different firmware. Test changes against a real
  TV when possible.

---

## 12. Development notes

- **Dependency:** `samsungtvws` is the **NickWaterton fork**, pinned to a
  specific commit SHA in `manifest.json`
  (`...samsung-tv-ws-api.git@<sha>`). The fork has no PyPI release and its
  `master` API shifts over time (e.g. the Dec 2025 upload-API rework), so we pin
  to a known-good commit for reproducible installs. **To upgrade:** read the
  fork's recent commits, verify the `art()` method signatures we call still
  match `api.py` (`upload`, `select_image`, `change_matte`, `set_artmode`,
  `get_artmode`, `get_current`, `get_thumbnail`, `available`, `delete`,
  `delete_list`), then update the SHA and bump the integration version. HA
  installs the dep into `deps`; `__init__.py` also adds the deps dir to
  `sys.path` and patches `helper.is_true` for older builds.
- **Quick sanity check** (no HA required):
  ```bash
  python3 -m py_compile custom_components/samsung_frame_art_director/*.py
  python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('custom_components/samsung_frame_art_director/**/*.json', recursive=True)]"
  ```
- **Versioning:** bump `manifest.json` `version` on user-facing changes.
- **No automated test suite exists yet.** The AI layer (`ai.py`) and pure
  helpers (preprocessing, tag filtering, DB dedup) are the most testable units
  if you want to start adding coverage.
- **Logs are the primary debugging tool** — filter HA logs by
  `samsung_frame_art_director`. Keep the existing debug breadcrumbs intact.
</content>
