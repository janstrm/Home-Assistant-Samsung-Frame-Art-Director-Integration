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

Runtime Art and remote-key behavior communicates through Samsung's **internal
WebSocket API**, via the
[`samsungtvws`](https://github.com/xchwarze/samsung-tv-ws-api) library (official
PyPI release `samsungtvws[async,encrypted]>=3.0.5`). An isolated
`ip_control.py` boundary implements the separate HTTPS JSON-RPC power protocol
on ports 1516/1515. Its user-initiated pairing and per-entry persistence are
available, and explicit targeted power-on, power-off, and reboot actions use
that boundary. There is no supported public Samsung consumer-TV API; these
paths are reverse-engineered and model-dependent. The established WebSocket behavior is
confirmed on the **Q65LS03DAU**; other models/years may differ.

Core capabilities:

- Toggle Art Mode on/off **with state verification**.
- Upload local images or fetch trusted HTTP(S) image URLs (auto
  center-cropped/resized to 3840×2160).
- Maintain a **local SQLite library** of art with AI-generated tags.
- **Rotate** displayed art on a schedule, filtered by tags / favorites / folder.
- **Clean up** the TV's limited internal storage.
- Request explicit IP Control panel power-on, power-off, or reboot after pairing.
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
│  text, sensor)  cleanup, IP     job)               ▼            │
│                 power/reboot)                      │            │
│      │             │                │         ai.py             │
│      └─────────────┴────────────────┘   (Gemini/OpenAI/Claude)  │
│                    │                               │            │
│                    ▼                               ▼            │
│         api.py: SamsungFrameClient        Gemini / OpenAI /     │
│         (async facade + SQLite DB)        Anthropic REST APIs   │
│                    │                                            │
└────────────────────┼────────────────────────────────────────────┘
                     │  samsungtvws (sync art() in executor)
                     ▼
        Samsung Frame TV  (WebSocket :8002/:8001, encrypted :8000)
        └─ paired-on-demand endpoint: HTTPS JSON-RPC :1516/:1515 (ip_control.py)
                     ▲
   /media/frame/inbox │ /media/frame/library  (HA filesystem)
```

Two external boundaries dominate the design:

1. **The TV** — runtime operations currently reach it through `samsungtvws`.
   IP Control is a separate dependency-free protocol boundary, constructed only
   by explicit pairing and power actions. Connections are
   short-lived, created per-operation, and wrapped in timeouts/retries. The
   library is partly synchronous, so blocking calls are pushed to threads. Device-info
   discovery uses the REST client directly so it cannot trigger WebSocket
   pairing. `SamsungTVWS.art()` creates a separate Art App child connection;
   that channel is intentionally opened without the remote-control token and
   closed before the parent TV client.
2. **The AI provider** — Gemini (default), OpenAI, or Anthropic, reached over
   HTTPS for image → tag analysis. Optional; only used by the curator.

---

## 3. Repository layout

```
custom_components/samsung_frame_art_director/
├── __init__.py        # Setup, teardown, service & WS-API registration, slideshow timer
├── api.py             # SamsungFrameClient: the core TV facade + SQLite library DB (~1.8k lines)
├── database.py        # Shared local-art + per-entry TV-state DB paths/migration
├── bridge.py          # Pairing/handshake + port/method selection (used by config_flow)
├── ip_control.py      # Isolated HTTPS JSON-RPC power client
├── ip_control_pairing.py # User-initiated port fallback + token acquisition
├── ip_control_actions.py # Explicit targeted power actions + HA error mapping
├── config_flow.py     # Pairing UI, zeroconf discovery, reauth, reconfigure, options
├── curator.py         # ContentCurator: inbox processing & library sync (AI tagging)
├── ai.py              # Provider registry + Gemini/OpenAI/Anthropic analyzer adapters
├── const.py           # Constants, option keys, defaults
├── file_access.py     # Canonical local-path boundary, opaque IDs, image MIME types
├── views.py           # HTTP view serving local thumbnails to the dashboard
├── media_source.py    # Media Source provider (browse library in the Media panel)
├── sensor.py          # Gallery library sensor (+ gallery page number)
├── image.py           # Live "current artwork" preview entity
├── media_player.py    # Main control entity (power/art-mode status)
├── switch.py          # slideshow_enabled, gallery_favorites_only, auto-brightness
├── select.py          # slideshow source; matte style/color; motion timer
├── number.py          # slideshow interval, gallery page, brightness, color temp, motion sensitivity
├── text.py            # free-text slideshow/tag filter
├── services.yaml      # Service schemas (UI metadata)
├── runtime.py         # Typed ConfigEntry runtime ownership
├── targets.py         # Target/registry resolution for loaded Frames
├── strings.json       # config/options flow strings
├── translations/en.json
├── manifest.json      # domain, version, requirements (samsungtvws>=3.0.5)
└── icon.png
examples/dashboard.yaml # Reference 3-column gallery dashboard
docs/ARCHITECTURE.md    # (this file)
```

---

## 4. Module reference

### `api.py` — `SamsungFrameClient`

The heart of the integration. A single instance per config entry, stored in the
typed `ConfigEntry.runtime_data.client`. It is an **async facade**: every public
method is `async`, and any blocking `samsungtvws` call is run via
`asyncio.to_thread` / `hass.async_add_executor_job`.

Responsibilities:

- **Startup authentication** — `async_connect_and_pair()` (saved-token
  validation, token rotation capture, DUID). It validates the persisted token
  without opening a pairing flow, and classifies failure rather than assuming
  unavailability. An explicit rejection raises `AuthenticationRejectedError`.
  A handshake that *hung* on a TV still answering its tokenless REST endpoint
  raises `PairingTimeoutError` (a subclass, so both reach Home Assistant as
  `ConfigEntryAuthFailed` and start reauth) — that combination is the on-screen
  approval dialog, which no amount of retrying can clear. Everything else,
  including a handshake that failed after the token was accepted, stays
  `DeviceUnavailableError` → `ConfigEntryNotReady`. The Art child normally
  uses the remote channel's port; if Samsung answers its tokenless handshake
  with `ms.channel.timeOut` or the WebSocket transport times out, startup tries
  the alternate 8001/8002 Art port and remembers it for later operations
  without moving the authenticated remote channel.
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

A single `asyncio.Lock` (`self._art_lock`) serializes every "art channel"
operation, including reads/polling, previews, settings, upload, select,
diagnostics, and cleanup, so concurrent calls don't collide on the TV's single
WebSocket art channel.

### `bridge.py`

Stateless pairing helpers used **only by the config flow**:
`async_probe_device_info()` (port detection), `async_try_connect()` (token
handshake, async-then-sync), and the encrypted-pairing pair
(`async_encrypted_start_pairing` / `async_encrypted_try_pin`) for legacy H/J
models. Returns `PairResult` objects with `RESULT_*` semantics from `const.py`.

### `ip_control.py` — isolated power-protocol boundary

Implements Samsung's separate, reverse-engineered HTTPS JSON-RPC endpoint on
port 1516 with strict response validation, bounded reads/timeouts, per-host
serialization, and credential-safe errors. `ip_control_pairing.py` constructs it
only after the user submits **Reconfigure → IP Control**, tries port 1515 only
after a transport failure on 1516, and returns the token plus successful port.
The config flow persists those values as `ip_control_token` and
`ip_control_port` on that TV's entry. Startup and polling never invoke pairing;
`ip_control_actions.py` constructs the client with only the selected entry's
credential and exposes explicit power-on, power-off, and reboot operations.
Authentication rejection starts a linked IP reauth repair. It does not replace
or modify any WebSocket Art Mode behavior.

### `config_flow.py`

- **`SamsungFrameConfigFlow`** — probes the host, picks a port, sets a unique id
  from the DUID, and runs either the standard pairing step or the encrypted PIN
  step (selected by model-name prefix `H`/`J`).
- **`OptionsFlowHandler`** — setup-time, rarely-changed config only: AI
  provider/keys/model, folders, image fit, cleanup, Wake-on-LAN, verbose
  logging. All fields are **optional** (nothing required) and grouped into
  collapsible **sections** (`data_entry_flow.section`) with proper selectors.
  Because sections return data **nested** under each section key, `async_step_init`
  **flattens** the payload (driven by `OPTION_SECTIONS`) and merges it into
  existing options so entity-managed keys are preserved. Slideshow/matte/favorites
  live as **entities**, not here. Option changes are hot-applied (slideshow timer +
  resize mode) **without** a full entry reload, to avoid an "unavailable" blip.
- **Config-entry migration** — `async_migrate_entry` in `__init__.py` upgrades to
  `VERSION = 3`: legacy `matte_enabled` → `matte_style`/`matte_color`, legacy
  `slideshow_source_dir` → `library_dir`. Idempotent; guarded on version.

### `curator.py` — `ContentCurator`

Owns the `/media/frame/inbox` → `/media/frame/library` pipeline. Built fresh per
service call. See [§6 flows](#process-inbox).

### `ai.py`

Vision tagging. `ImageAnalyzer` is the interface; `GeminiAnalyzer` (default),
`OpenAIAnalyzer`, and `AnthropicAnalyzer` are adapters. `AI_PROVIDER_SPECS` and
`create_analyzer()` are the single wiring point between options and adapters —
see [§8](#8-ai-tagging-layer).

### Entity platforms

All entities are thin and read from / write to either the `ConfigEntry.options`
or the `SamsungFrameClient`. Every entity uses `_attr_has_entity_name = True` and
attaches to the one device via `DeviceInfo` keyed on the DUID. Behaviour-config
entities (slideshow source/interval/filter/enabled, matte style/color,
favorites-only, gallery page) are marked `EntityCategory.CONFIG` so they group
under *Configuration* on the device page; the actual controls (media_player,
art preview, brightness, color temperature) stay primary. Notable:

- `media_player.py` — primary entity; exposes `art_mode_status` attribute.
- `image.py` — serves `async_get_current_art()` bytes as a camera-style preview.
- `sensor.py` — `..._art_library`: state = item count, `items` attribute = one
  bounded gallery page for the dashboard.

---

## 5. Data model (SQLite)

The database model deliberately has two scopes:

- `<config>/samsung_frame_director/art_library.db` owns the shared `local_art`
  table because the files on Home Assistant belong to the installation, not to
  one TV.
- `<config>/samsung_frame_director/art_library_<config-entry-id>.db` owns the
  `art_library` table for one Frame. This keeps Samsung content IDs, TV
  presence, favorites, and cleanup state isolated even when two TVs return the
  same ID.

On the first start after this change, `database.py` copies the former shared DB
into each entry-owned database with SQLite's backup API and an atomic rename,
then removes `local_art` from that copy. The original remains the shared local
library and a recovery source. The client attaches it to each short-lived
per-entry connection so existing queries can combine local files with the
selected TV's state. Config-entry setup eagerly prepares and migrates both
schemas through `async_initialize_database()` so a broken DB stops setup
visibly; public DB operations still call `_ensure_db()` defensively.

There are **two tables**, and understanding the split is key:

### `local_art` — source of truth for the library

Rows represent **image files on the HA filesystem** (in `/media/frame/library`),
tagged by AI. This is what the gallery sensor, dashboard, and rotation primarily
read from.

The filesystem path remains the database key but is never exposed as a media or
thumbnail identifier. Public gallery items use a stable opaque `local-…` ID;
every read or delete resolves that ID back through `local_art`, canonicalizes the
path, and verifies it remains below an allowed Home Assistant media/config root.

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
> keep those old columns as harmless leftovers; their values are copied into
> `created_at`/`last_displayed_at`/`source_file`. Existing source identities are
> canonicalized once in place, and the SQLite `user_version` records that data
> migration without deleting rows.

### Why two tables?

`local_art` = "what this HA installation has on disk and could show on any
Frame". `art_library` = "what is currently/previously on this TV's limited
internal storage". Rotation can either
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
4. On every later setup, `async_connect_and_pair()` reuses the exact saved
   `(client name, token)` identity and validates it through an authenticated Art
   child. It never opens a token-file pairing flow during a normal restart, and
   public REST DUID data alone cannot mark setup successful. An explicit
   `UnauthorizedError` becomes `AuthenticationRejectedError` and then
   `ConfigEntryAuthFailed`, which starts the **reauth flow**
   (`async_step_reauth` → `async_step_reauth_confirm`) once. Offline/timeouts or
   missing device info become `ConfigEntryNotReady`, so HA retries without an
   approval prompt. A rotated token is persisted and the obsolete pairing file
   is removed after successful validation.

User-entered hosts are cleaned by `_normalize_host()` (trims whitespace,
strips a `scheme://`, path, and trailing `:port`) before probing.

Optional IP Control pairing is a separate, explicit Reconfigure menu path. The
form tells users to enable IP Remote, leave Art Mode, and use a trusted local
network. `createAccessToken` runs only after form submission. The resulting
credential is stored per entry and separately from the WebSocket token; a later
submission safely replaces a stale IP token without changing other entry data.
Transport failure may fall back from 1516 to 1515, but an application-level TV
response never triggers a second endpoint or approval prompt. An action token
rejection starts Home Assistant reauth with
`reauth_connection=ip_control`; the flow then links directly to the same safe
pairing form instead of entering WebSocket reauth.

### Explicit IP Control power actions

The domain actions `power_on`, `power_off`, and `reboot` resolve targets through
`async_resolve_action_targets()` and build a short-lived IP Control client from
only that selected entry's `ip_control_token` and `ip_control_port`. An unpaired
entry fails validation before any network call. A rejected token starts the
linked IP Control repair flow; transport, state/model, and protocol failures
remain distinct user-facing validation errors without exposing raw responses.

These actions are deliberately separate from the media-player power methods.
`media_player.turn_on` and `turn_off` still enter and leave Art Mode through the
established WebSocket client. When explicit power-on finds the paired IP Control
port asleep, it may fall back to the entry's already configured Wake-on-LAN MAC
address. There is no IP Control polling or Art Mode routing.

### Set Art Mode (with verification)

`async_set_artmode(enabled)` → `_async_set_artmode_locked()` (under `_art_lock`):

1. **Early-exit** if already in the desired state.
2. Open a short-lived synchronous `SamsungTVWS` client in a worker thread and
   call `art().set_artmode()`.
3. Verify `get_artmode()` up to 3× with 2s spacing. On *enable*, if verification
   fails, force-`select_image()` a candidate to coax Art Mode on.
4. Optional service-layer extras (in `__init__.py`): **Wake-on-LAN** before ON,
   and a **POWER key** fallback when a fully-off TV does not wake into Art Mode
   or when OFF is not applied.

### Upload an image

The `upload_art` service obtains the source bytes, then calls
`async_upload_image(bytes, matte, source_file, tags) -> content_id | None`:

1. Read a sandboxed local `/media`/`/config` path off-loop, or fetch an HTTP(S)
   URL allowed by Home Assistant's `allowlist_external_urls` through its shared
   aiohttp client with a 30-second timeout and 20 MiB streaming limit. Redirects
   are followed manually (maximum five) and each destination is revalidated;
   embedded credentials and unsupported schemes are rejected before I/O.
2. Canonicalize the source identity (local path aliases resolve to one absolute
   path; URL scheme/host/default ports/path aliases normalize while the query is
   preserved), then look up every tracked `content_id` for that identity in the
   selected Frame's entry-owned database. Ask that TV for its available art
   (30-second socket timeout) and fast-select the first matching
   ID. The blocking worker is cancellation-contained: its caller retains
   `_art_lock` until the worker exits, so it cannot perform a late selection
   beside a subsequent Art operation. Update its tags and return without
   uploading. Only a successfully confirmed absence permits a new upload; DB
   lookup, TV check, and selection failures abort to avoid duplicate copies.
   The check is repeated under `_art_lock` immediately before upload so
   concurrent calls for one source cannot both create a copy.
3. `async_preprocess_image()` — Pillow: scale-to-fill + center-crop to
   **3840×2160**, JPEG q85.
4. Under `_art_lock`, use the synchronous, tokenless Art API on the working
   8001/8002 port in a worker thread to upload, select, and apply the landscape
   matte. On samsungtvws
   versions whose `select_image()` has no `matte` argument, fall back to
   `change_matte(content_id, matte_id=...)` without overwriting the optional
   portrait matte; LS03D/LS03F reject that extra parameter with error `-7`.
5. Retry up to 5× with exponential backoff, priming the art channel before
   each attempt and recreating the client on `ConnectionFailure`.
6. Track the exact TV-returned `content_id` once in `art_library`, together with
   its tags and source path/URL, and return it to the service. With Home
   Assistant's optional service response enabled, callers receive `content_id`
   for a single target and `content_ids` for all targets.

### Process Inbox {#process-inbox}

`ContentCurator.async_process_inbox()`:

1. Build the analyzer via `_build_analyzer()`; bail with a notification if no
   key/provider is configured.
2. List images in `/media/frame/inbox`.
3. For each: validate bytes/dimensions and **analyze first** through the selected
   provider. Provider-wide authentication, configuration, rate-limit, timeout,
   and availability failures stop the batch; image-specific failures skip one file.
4. Only after a successful analysis: **move** the file to
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

`async_cleanup_storage()` reads the TV's current + available content and always
restricts deletion candidates to rows with a non-empty `source_file` (proof of
integration upload provenance). Missing or unreadable provenance fails closed,
so manual *My Photos* and Art Store items cannot be deleted. It **preserves
favorites and the current image**, applies optional age and `max_items` limits
(counting only deletion-eligible integration uploads and deleting oldest first),
then deletes via `delete_list` (fallback: per-id `delete`) and reconciles the
`on_tv` flags in the DB. If current art is unknown initially or at the final
pre-delete check, it deletes nothing. `dry_run` follows that same planning path.
Saved cleanup options are used consistently after uploads, slideshows, and the
manual cleanup action unless that action explicitly overrides a value.

---

## 7. The resilience layer

> If you remember one thing from this document: the apparent over-engineering in
> `api.py` is deliberate. Samsung's API is inconsistent across firmware/models,
> and the same logical call can fail in different ways. Removing a fallback will
> "work on my TV" and break on someone else's.

Patterns you will see repeated, and why they exist:

- **Stable remote identity (avoids recurring pairing popups).** The Frame's
  remote-control channel ties authorization to the `(client name, token)` pair.
  All sync parent clients are therefore built through
  `SamsungFrameClient._make_tv()` (always passes `name` + `token`), and
  `_capture_token()` runs on close to **persist any token the TV re-issues** (via
  a loop-safe `set_token_persister` callback wired in `__init__.py`) so
  authorization doesn't drift. The separate `com.samsung.art-app` child is
  built through `_make_art()` and deliberately has `token` and `token_file`
  cleared: newer Frame firmware can stall that handshake when it receives the
  remote-control token. Never construct either client outside these helpers.
- **Connection model.** Every art operation opens a short-lived `SamsungTVWS`
  parent and uses a tokenless synchronous `art()` child off the event loop via
  `asyncio.to_thread` (`async_get_state`, `_async_art`, `async_upload_image`,
  `async_set_artmode`, …). Each parent creates exactly one Art child;
  `_close_art_connection()` captures any refreshed token and closes child then
  parent exactly once. There is no long-lived connection; the `_art_lock`
  serializes reads and writes alike. Startup first authenticates the remote
  parent and then verifies that the tokenless Art child is usable.
- **Pairing vs. art operations.** `bridge.py` uses the official async/encrypted
  remote client for pairing and an authenticated sync remote client only as a
  compatibility fallback. Runtime Art API calls use short-lived tokenless sync
  Art clients in worker threads because the full Art Mode settings API is
  exposed there.
- **Port selection.** Pairing probes the ports supported by the TV. The
  authenticated remote channel retains that selected port, while the tokenless
  Art channel may fall back independently between 8001 and 8002 after an
  explicit Samsung `ms.channel.timeOut` response or a transport-level timeout.
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
- **No token material in logs.** Integration messages expose only whether a
  token exists. The `samsungtvws.connection` logger remains at WARNING because
  version 3.0.5 includes raw token values in lower-level connection messages.

When changing this layer, prefer **adding** a guarded path over removing one,
and keep the debug logging — it is the only diagnostic tool users have.

---

## 8. AI tagging layer

`ai.py` defines:

- `ImageAnalyzer` (ABC) — `analyze_image(bytes, prompt, api_key=...) -> dict`
  returning
  `{tags, description, provider, model, duration}` or `{error}`.
- `GeminiAnalyzer` — Google Gemini via REST over Home Assistant's shared aiohttp
  session, default model `gemini-2.5-flash`. The API key travels in the
  `x-goog-api-key` header, never in the request URL or analyzer state.
  Prompts for ~15 keywords including weather/lighting/mood.
- `OpenAIAnalyzer` — OpenAI vision through Chat Completions over the same shared
  aiohttp session; the configured model defaults to `gpt-4o`.
- `AnthropicAnalyzer` — Claude vision through the Messages endpoint, defaulting
  to `claude-haiku-4-5-20251001`; Anthropic's smaller direct-request image limit
  is enforced before submission.
- All adapters receive one shared JSON tag/description contract and normalize
  responses to the same result shape.
- `AI_PROVIDER_SPECS` records each provider's credential option, model option,
  default model, and adapter. `create_analyzer(provider, model, session=...)` is
  the **factory** and the only
  place that maps the `ai_provider` option to a concrete class. Returns
  `(analyzer, error)`. The curator supplies the selected credential only to the
  individual `analyze_image` call; analyzer objects never retain it. Both
  providers disable redirects and use fixed HTTPS endpoints with 30-second
  timeouts.

Before either provider is called, `curator.py` reads at most 20 MiB off-loop,
detects JPEG/PNG/WebP from the byte signature, verifies the image with Pillow,
and rejects images over 40 megapixels or 16,384 pixels on either side. Provider
response bodies and exception text are not copied into logs or user-facing
errors.

The curator never instantiates a concrete analyzer directly; it calls
`self._build_analyzer()` → `create_analyzer()`. **To add a provider:** implement
an `ImageAnalyzer` subclass, add a branch in `create_analyzer()`, add the
provider constant in `const.py`, and add its option(s) to the options flow in
`config_flow.py` (+ `strings.json` / `translations/en.json` labels).

---

## 9. Services, entities & the dashboard

Service schemas live in `services.yaml`; handlers, the thumbnail view, and the
WebSocket command are registered once in domain `async_setup()` (a single
source of truth). Every Frame action resolves its entity target through
`targets.async_resolve_action_targets()` to a loaded ConfigEntry runtime. With
exactly one loaded Frame, a missing target selects it for backward
compatibility; with zero or multiple loaded Frames, or an invalid/unloaded
target, the action raises `ServiceValidationError`. Internal coordination finds
renamable entities through their stable registry unique IDs. `media_player.py`
deliberately does **not** register entity-platform services for the same names
(it would double-register and diverge); it only implements the native
`turn_on`/`turn_off` Art-Mode toggle.

A **WebSocket command** `samsung_frame_art_director/get_library` and the
`SamsungFrameThumbnailView` HTTP view feed the example gallery dashboard. The
command accepts `config_entry_id` and requires it when multiple Frames are
loaded; thumbnail routes include the same entry ID. The
gallery is also exposed via the `..._art_library` sensor's `items` attribute for
template/auto-entities use. Thumbnail requests require Home Assistant
authentication; gallery and Media Source results carry short-lived signed paths
so browser image requests work without exposing an unauthenticated endpoint.
The full user-facing service/entity catalog is in
the [README](README.md#-services).

---

## 10. Concurrency & threading model

- The integration is **async**; HA's event loop must never be blocked.
- All `samsungtvws` and filesystem/Pillow calls run off-loop via
  `asyncio.to_thread` or `hass.async_add_executor_job`.
- TV art-channel operations are serialized with `SamsungFrameClient._art_lock`.
- Timed synchronous socket calls run through
  `_async_run_blocking_contained()`. Because a Python worker thread cannot be
  cancelled, timeout/cancellation drains that worker before the surrounding
  lock or port attempt can continue; a late operation cannot overlap its
  successor.
- SQLite is accessed with short-lived per-call connections inside executor jobs
  (`_get_db()` / `sqlite3.connect`), avoiding cross-thread connection sharing.
  `_get_db()` attaches the shared local-art DB to the selected Frame's isolated
  TV-state DB.
- Network calls use explicit client timeouts plus an aggregate timeout, either
  `_async_run_blocking_contained()` for synchronous TV calls or
  `aiohttp.ClientTimeout` for HTTP (typically 10–120s).

---

## 11. Known quirks & gotchas

- **`art_library` schema is two-place** — `CREATE TABLE` *and* the `ALTER`
  migrations must stay in sync (see [§5](#5-data-model-sqlite)). Adding a column
  in only one place silently breaks either fresh installs or upgrades.
- **`async_rotate_art_now()` legacy modes.** Its `library` and `aware` modes are
  unimplemented no-ops; the live rotation path is `async_rotate_art()` /
  `async_rotate_from_folder()`. Don't confuse the two.
- **Configured media paths.** `/media/frame/inbox` and `/media/frame/library`
  are defaults. The options flow can override both; every resolved path still
  has to remain under Home Assistant's allowed config/media roots.
- **Verbose logging on by default.** `diagnostics_verbose` defaults to `True`
  and bumps several loggers to DEBUG; this is deliberate for field debugging.
- **Per-model variance.** Anything in [§7](#7-the-resilience-layer) may behave
  differently on non-Q-series / different firmware. Test changes against a real
  TV when possible.

---

## 12. Development notes

- **Dependency:** `samsungtvws[async,encrypted]>=3.0.5` from PyPI (the official
  [xchwarze](https://github.com/xchwarze/samsung-tv-ws-api) package, which gained
  full Art Mode support in the 3.0 line). All art calls go through the sync
  `SamsungTVWS.art()` API run in executor threads; `bridge.py` uses the async
  remote/encrypted classes for pairing only. **To upgrade:** bump the minimum and
  verify the `art()` method signatures we call still match `api.py` (`upload`,
  `select_image`, `change_matte`, `set_artmode`, `get_artmode`, `get_current`,
  `get_thumbnail`, `available`, `delete`, `delete_list`, `get_brightness`,
  `set_brightness`, `get_color_temperature`, `set_color_temperature`,
  `set_motion_timer`, `set_motion_sensitivity`, `set_brightness_sensor_setting`,
  `get_artmode_settings`). HA installs the dep into `deps`; `__init__.py` also
  adds the deps dir to `sys.path` and patches `helper.is_true` for older builds.
- **Quick sanity check** (no HA required):
  ```bash
  python3 -m py_compile custom_components/samsung_frame_art_director/*.py
  python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('custom_components/samsung_frame_art_director/**/*.json', recursive=True)]"
  ```
- **Versioning:** bump `manifest.json` `version` on user-facing changes.
- **Automated tests:** `pytest` covers service behavior, TV-client boundaries,
  preprocessing, tag filtering, and DB helpers; Ruff and pytest run in CI.
- **Logs are the primary debugging tool** — filter HA logs by
  `samsung_frame_art_director`. Keep the existing debug breadcrumbs intact.
</content>
