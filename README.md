<p align="center">
  <img src="https://raw.githubusercontent.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/main/custom_components/samsung_frame_art_director/icon.png" alt="Samsung Frame Art Director" width="200" style="border-radius:20px"/>
</p>

# 🖼️ Samsung Frame Art Director
> **A Custom Integration for Home Assistant to control Samsung Frame TV Art Mode.**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Latest release](https://img.shields.io/github/v/release/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration)](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/releases/latest)
[![Tests](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/actions/workflows/test.yml/badge.svg)](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/actions/workflows/test.yml)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=janstrm&repository=Home-Assistant-Samsung-Frame-Art-Director-Integration&category=integration)

Control your Samsung Frame TV's Art Mode directly from Home Assistant. Upload local images or fetch them from trusted HTTP(S) URLs with automatic resizing, rotate art on a schedule, manage TV storage, and build gallery dashboards. Optional cloud classification through Google Gemini, OpenAI, or Anthropic can auto-tag images placed in an inbox folder.

---

## 📋 Prerequisites

- **Home Assistant** (Core, Supervised, or OS)
- **HACS** (Home Assistant Community Store) installed.
- A **Samsung Frame TV** connected to the same local network.
- (Optional) A **Google Gemini, OpenAI, or Anthropic API key** for automatic image tagging.

---

## ✨ Capabilities

- **State Verification:** Toggles Art Mode ON/OFF and verifies the state to ensure the screen displays art rather than just being powered down.
- **Image Uploads:** Upload local images or fetch them from a trusted HTTP(S) URL. Images are resized to 3840×2160 before upload (choose **crop** to fill or **fit** to letterbox in the options).
- **Auto-Tagging (Optional):** Drop images into an inbox folder and run **Process Inbox**. The selected provider analyzes, tags, and catalogs them in your local library.
- **Gallery Sensor:** Exposes a database of your local art, allowing you to build dashboard views with the provided example YAML.
- **Media Browser:** Browse your tagged library in Home Assistant's **Media** panel and "play" any image to the Frame (it uploads and displays it).
- **Auto-Rotation:** Rotates art from local storage or limits selection based on assigned tags, favorites, and filters.
- **Favorites:** Mark individual artworks as favorites. Filter the gallery or rotation to only use your favorite pieces.
- **Storage Management:** Detects and deletes orphaned or un-favorited artworks from the TV memory to manage limited storage capacity.

---

## 🚀 Installation

### Method 1: HACS (recommended)

Use the **Open in Home Assistant** button above. If you prefer to add the repository manually:

1. Open HACS in Home Assistant.
2. Open the three-dot menu and select **Custom repositories**.
3. Add `https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration` as an **Integration**.
4. Open **Samsung Frame Art Director** and select **Download**.
5. Restart Home Assistant.
6. Open **Settings → Devices & services**, select **Add integration**, and search for **Samsung Frame Art Director**.

### Method 2: Manual installation
1. Download this repository.
2. Copy the `custom_components/samsung_frame_art_director/` folder into your Home Assistant `/config/custom_components/` directory.
3. Restart Home Assistant.

---

## ⚙️ Configuration

If your TV is on, Home Assistant usually discovers it automatically. Look for **Samsung Frame Art Director** under **Settings → Devices & services** and select **Configure**. Otherwise select **Add integration** and search for **Samsung Frame Art Director**.

### Initial setup
- You will be asked for the TV's IP address and a Name (pre-filled when discovered).
- Follow the prompt on your TV to "Allow" the connection.
- If the TV's IP later changes, use **Reconfigure → TV Connection** on the integration to update it (no need to delete and re-add); discovery also updates it automatically.

### Pair optional IP Control

IP Control is Samsung's separate local connection for true panel power commands. To pair it, open the integration's **Reconfigure → IP Control** flow. First enable **IP Remote** in the TV settings, wake the TV, and switch to normal TV viewing rather than Art Mode. Click **Submit** only when you are ready to approve the prompt on the TV.

The IP Control token and selected port are stored only in that TV's Home Assistant config entry, separately from the normal WebSocket token. Pairing is never started by a restart, polling, or an action. Because the TV uses a self-signed certificate, use this feature only on a trusted local network. Support varies by model and firmware.

After pairing, 3 explicit targeted actions are available: **Power On (IP Control)**, **Power Off (IP Control)**, and **Reboot (IP Control)**. The IP Control Power Off action requests true panel standby; the existing media-player **Turn Off** still only leaves Art Mode, exactly as before. If the paired IP Control port is unreachable in standby, Power On falls back to Wake-on-LAN when its existing option is enabled and a TV MAC address is configured. Power-on from standby still depends on the Frame model, firmware, network, and energy settings. A rejected saved token creates a Home Assistant repair that links back to the safe pairing form.

### Configure optional features
The integration works with its defaults after pairing. Select **Configure** to adjust optional settings, grouped into collapsible sections:
- **AI Image Tagging:** Select Google Gemini, OpenAI, or Anthropic. Enter that provider's API key and optionally choose its model. **Process Inbox** and **Sync Library** send the full image to the selected cloud provider.
- **Storage Cleanup:** Max items / age to keep on the TV, preserve-current, dry-run.
- **Folders & Image:** Inbox/library folder paths and the image fit mode (crop vs. fit/letterbox).
- **Connection & Power:** Wake-on-LAN MAC and power-key fallback.
- **Advanced:** Art Mode setting entities and verbose logging.

> Slideshow, matte, and favorites use their own switch, select, number, or text entities. Control them from dashboards and automations instead of this dialog.

#### Cloud tagging providers

Cloud tagging is disabled until you select a provider and save its API key. The default models accept image input and balance speed with cost:

| Provider | Default model | API key |
|---|---|---|
| Google Gemini | `gemini-2.5-flash` | [Create a key in Google AI Studio](https://aistudio.google.com/app/apikey) |
| OpenAI | `gpt-4o` | [Create an OpenAI API key](https://platform.openai.com/api-keys) |
| Anthropic | `claude-haiku-4-5-20251001` | [Create a key in Anthropic Console](https://console.anthropic.com/settings/keys) |

You may enter another model ID for the selected provider. The model must accept image input and return text. Provider usage may incur charges under your account. The full image leaves Home Assistant during classification; API keys and provider response bodies are excluded from logs and returned errors.

---

## 📂 Folder structure

The integration uses two folders on your HA filesystem:

| Folder | Purpose |
|---|---|
| `/media/frame/inbox` | Drop new images here. **Process Inbox** will analyze, tag, and move them to the library. |
| `/media/frame/library` | Permanent storage for processed images. Used by rotation and the gallery sensor. |

### Workflow
1. Drop images into `/media/frame/inbox/`
2. Run **Process Inbox** → the selected provider tags each image, then the integration moves it to `/media/frame/library/`
3. Images appear in the Gallery sensor and are available for rotation
4. If you add images directly to `/media/frame/library/`, run **Sync Library** to tag and register them

---

## 🖥️ Dashboard examples

The included dashboard YAML combines TV controls and the art gallery in a three-column view.

You can find the code here: [`examples/dashboard.yaml`](examples/dashboard.yaml)

To use the Art Gallery with popups, you will need these HACS frontend plugins:
1. **[auto-entities](https://github.com/thomasloven/lovelace-auto-entities)** (For the dynamic image gallery grid)
2. **[browser_mod](https://github.com/thomasloven/hass-browser_mod)** (For clicking an image to open the Push/Favorite/Delete popup)
3. **[card-mod](https://github.com/thomasloven/lovelace-card-mod)** *(Optional)* (For visual enhancements like favorite indicators)

Create a dashboard in Home Assistant, open **Edit → Raw configuration editor**, and paste the example file. Home Assistant arranges the three columns on wide screens.

### 🧪 Testing dashboard (no extra plugins)

To exercise common entities and actions from one screen with built-in Lovelace cards, paste [`examples/testing_dashboard.yaml`](examples/testing_dashboard.yaml) into a new dashboard's **Raw configuration**. It includes the art preview, Art Mode toggle, brightness, color, motion, matte, slideshow, library sensor, Media browser, rotation, inbox processing, sync, cleanup, diagnostics, and database purge controls.

> Entity IDs in both examples assume the prefix `samsung_frame`. If your device uses a different prefix (e.g. `65_the_frame_…`), find‑replace it (check **Developer Tools → States**).

---

## 🎮 Actions

Domain: `samsung_frame_art_director`

Every action below is resolved to a loaded Frame at call time. If exactly one
Frame is loaded, `target` may be omitted for backward compatibility. If two or
more Frames are loaded, provide a `media_player` under `target`; otherwise Home
Assistant returns a clear validation error instead of choosing a TV. Renaming
an entity is safe because the integration follows its stable registry identity.

### Core actions

#### set_artmode
Toggle Art Mode on or off.
```yaml
action: samsung_frame_art_director.set_artmode
target:
  entity_id: media_player.samsung_frame
data:
  enabled: true
```

#### send_key
Send a Samsung remote-control key to one Frame. `hold_seconds` is optional;
when present, the integration sends a real press, waits for the requested
duration, and then releases the key. Valid durations are 0.1–30 seconds.

Tap HDMI:
```yaml
action: samsung_frame_art_director.send_key
target:
  entity_id: media_player.samsung_frame
data:
  key: KEY_HDMI
```

Hold volume up for 0.8 seconds:
```yaml
action: samsung_frame_art_director.send_key
target:
  entity_id: media_player.samsung_frame
data:
  key: KEY_VOLUP
  hold_seconds: 0.8
```

#### power_on

Request true panel power-on through paired IP Control. If the IP Control port is unreachable in standby, the action uses Wake-on-LAN when you configured a TV MAC address and enabled the fallback.

```yaml
action: samsung_frame_art_director.power_on
target:
  entity_id: media_player.samsung_frame
```

Power-on support from standby depends on the Frame model, firmware, network, and energy settings.

#### power_off

Request true panel standby through paired IP Control. This differs from the media-player **Turn off** command, which leaves Art Mode without guaranteeing panel standby.

```yaml
action: samsung_frame_art_director.power_off
target:
  entity_id: media_player.samsung_frame
```

#### reboot

Request a panel reboot through paired IP Control:

```yaml
action: samsung_frame_art_director.reboot
target:
  entity_id: media_player.samsung_frame
```

Pair IP Control under **Reconfigure → IP Control** before using these three actions.

#### upload_art
Upload and immediately display an image from your HA filesystem, an opaque
`local-…` gallery ID, or a trusted HTTP(S) URL. Remote downloads have a
30-second timeout and a 20 MiB size limit.

Local file:
```yaml
action: samsung_frame_art_director.upload_art
target:
  entity_id: media_player.samsung_frame
data:
  path: /media/frame/library/example.jpg
```

Tracked gallery item (the dashboard uses this form so it never exposes a path):
```yaml
action: samsung_frame_art_director.upload_art
target:
  entity_id: media_player.samsung_frame
data:
  path: "local-<opaque-library-id>"
```

Remote file:
```yaml
action: samsung_frame_art_director.upload_art
target:
  entity_id: media_player.samsung_frame
data:
  path: https://render-host.local/example.jpg
  tags: "dashboard, morning"
response_variable: upload_result
```

Before using a remote source, explicitly trust its URL prefix in Home
Assistant's `configuration.yaml`, then restart Home Assistant:

```yaml
homeassistant:
  allowlist_external_urls:
    - "https://render-host.local/"
    - "http://192.168.68.20:8080/"
```

Local paths must reside in `/media` or `/config`. Home Assistant fetches remote
URLs directly from its network, rejects embedded credentials and non-HTTP(S)
schemes, and rechecks every redirect against `allowlist_external_urls`. Keep the
allowed prefix as narrow as your renderer permits. The 30-second timeout and
20 MiB streaming limit also apply when a server omits or misreports its size.
When `response_variable` is requested, `upload_result.content_id` contains the
exact TV content ID for a single target and `upload_result.content_ids` contains
all returned IDs when multiple Frames are targeted.

Repeated calls with the same path or URL reuse the image already stored on the
target Frame instead of uploading another copy. The integration verifies the
stored content ID against each target TV, so a mapping from a different Frame
cannot suppress a required upload. If the image was removed from that TV, it is
uploaded again automatically.

#### rotate_art_now
Force an immediate rotation of the displayed art. Picks a random image from the library (optionally filtered by tags). Automatically retries if a selected image no longer exists on disk.
```yaml
action: samsung_frame_art_director.rotate_art_now
target:
  entity_id: media_player.samsung_frame
data:
  source: library       # library | folder
  tags: "nature, ocean" # Optional: only rotate images matching these tags
  match_all: false      # Optional: require ALL tags to match (default: any)
```

#### rotate_favorites
Rotate art but only pick from images marked as favorites.
```yaml
action: samsung_frame_art_director.rotate_favorites
target:
  entity_id: media_player.samsung_frame
```

### Library actions

#### process_inbox
Scan `/media/frame/inbox`, analyze each image with the configured Gemini, OpenAI, or Anthropic provider, move it to `/media/frame/library`, and register it with normalized tags.
```yaml
action: samsung_frame_art_director.process_inbox
target:
  entity_id: media_player.samsung_frame
```
> **Note:** Requires the selected provider's API key in the integration options.
> The full image leaves Home Assistant and is sent to that provider. JPEG, PNG
> and WebP inputs are validated before submission and normally limited to 20 MiB,
> 40 megapixels and 16,384 pixels on either side. Anthropic's direct API has a
> stricter request limit, so its inputs are capped at 7 MiB and 8,000 pixels per
> side. Authentication, model, rate-limit, timeout, and provider-wide failures
> stop the batch instead of retrying every remaining image. API keys and provider
> response bodies are never included in logs or returned errors.

#### sync_library
Reconcile the library folder with the local database:
1. **Deduplicates** the database (removes duplicate entries, keeps newest)
2. **Removes stale entries**: database records whose files no longer exist on disk
3. **Adds untracked images**: files in `/media/frame/library/` that are not yet in the database, tagged by the selected provider
```yaml
action: samsung_frame_art_director.sync_library
target:
  entity_id: media_player.samsung_frame
```
> **Note:** Phases 1 & 2 (cleanup) always run without AI. Phase 3 requires the selected provider's API key.

#### purge_database
Wipe the local SQLite database (art history, tags, favorites). **Does NOT delete image files** from `/media/frame/library/`.
```yaml
action: samsung_frame_art_director.purge_database
target:
  entity_id: media_player.samsung_frame
```
> **Tip:** After purging, run **Sync Library** to re-scan and re-tag your existing images.

### Gallery management actions

#### toggle_favorite
Toggle the favorite status of an artwork in the library database.
```yaml
action: samsung_frame_art_director.toggle_favorite
target:
  entity_id: media_player.samsung_frame
data:
  content_id: "local-<opaque-library-id>"
```

#### delete_art
Permanently delete a tracked local artwork file and its library records. Use
the opaque `local-…` ID exposed by `sensor.samsung_frame_art_library`; raw file
paths and untracked files are rejected.
```yaml
action: samsung_frame_art_director.delete_art
target:
  entity_id: media_player.samsung_frame
data:
  content_id: "local-<opaque-library-id>"
```

Gallery thumbnails use short-lived Home Assistant signed URLs. Filesystem paths
are never placed in gallery attributes, thumbnail URLs, or Media Source
identifiers.

#### cleanup_storage
Remove non-favorite artworks from the **TV's internal storage** to free up space.
```yaml
action: samsung_frame_art_director.cleanup_storage
target:
  entity_id: media_player.samsung_frame
data:
  max_items: 50                  # Optional: keep at most N items
  max_age_days: 30               # Optional: delete items older than N days
  preserve_current: true # Optional: don't delete the currently displayed artwork
  dry_run: false         # Optional: preview what would be deleted without actually deleting
```

Cleanup is fail-safe: only artworks with integration upload provenance
(`source_file`) can be deleted. Manually uploaded *My Photos* and Art Store
items are protected even if the legacy `only_integration_managed` option is
set to `false` in an older automation, or provenance data cannot be read. The
legacy option is still accepted for compatibility but is no longer shown in
new service and options forms. `max_items` counts only deletion-eligible uploads
managed by this integration, not protected TV art. With `preserve_current`
enabled, an unknown current artwork aborts cleanup without deleting anything;
`dry_run` performs the same safety checks and reports that plan without applying
it. The saved cleanup policy is also used after uploads and slideshows and by a
manual cleanup action unless the action explicitly overrides a value.

#### change_gallery_page

Move the dashboard gallery forward or backward. Use a positive `step` to move forward and a negative value to move backward:

```yaml
action: samsung_frame_art_director.change_gallery_page
target:
  entity_id: media_player.samsung_frame
data:
  step: 1
```

### Diagnostics

#### art_diagnostics
Log Art Mode support status, current artwork, and a sample of available content IDs (useful for debugging).
```yaml
action: samsung_frame_art_director.art_diagnostics
target:
  entity_id: media_player.samsung_frame
```

---

## 📊 Entities

When configured, the integration creates the following entities (where `samsung_frame` is your configured device name):

### Media player
| Entity | Description |
|---|---|
| `media_player.samsung_frame` | Main control entity. State reflects Art Mode. Attributes include `art_mode_status` and the current `content_id`. Supports browse/play from the Media panel. |

### Image
| Entity | Description |
|---|---|
| `image.samsung_frame_art_preview` | Live preview of the currently displayed artwork on the Frame TV. |

### Switches
| Entity | Description |
|---|---|
| `switch.samsung_frame_slideshow_enabled` | Enable/disable automatic art rotation. |
| `switch.samsung_frame_gallery_favorites_only` | Restrict the gallery and rotation to only favorited images. |
| `switch.samsung_frame_auto_brightness` | Art Mode auto-brightness (the TV's light sensor). |

### Select entities
| Entity | Description |
|---|---|
| `select.samsung_frame_slideshow_source` | Choose rotation source: `Library`, `Folder`, or `Tags`. |
| `select.samsung_frame_matte_style` | Matte (border) style: `none`, `modern`, `shadowbox`, `flexible`, etc. Set to `none` to disable the matte. |
| `select.samsung_frame_matte_color` | Matte (border) color: `polar`, `apricot`, `navy`, etc. Combined with the style as `{style}_{color}` (e.g. `shadowbox_polar`). Ignored when style is `none`. |
| `select.samsung_frame_motion_timer` | Art Mode motion auto-off timer: `off`, `5`, `15`, `30`, `60`, `120`, `240` (minutes). |

### Number entities
| Entity | Description |
|---|---|
| `number.samsung_frame_slideshow_interval` | Custom rotation interval in minutes (0–1440). |
| `number.samsung_frame_art_mode_brightness` | Art Mode brightness (0–10). |
| `number.samsung_frame_art_mode_color_temperature` | Art Mode color temperature (−5…5). |
| `number.samsung_frame_motion_sensitivity` | Art Mode motion-sensor sensitivity (1–3). |

### Text entities
| Entity | Description |
|---|---|
| `text.samsung_frame_slideshow_filter` | Free-text filter for tags or folder path used by rotation. |

### Sensors
| Entity | Description |
|---|---|
| `sensor.samsung_frame_art_library` | Reports total tracked artworks. Live attributes include the paged `items` list for dashboard gallery rendering; the volatile gallery payload is excluded from Recorder history. |

---

## 🔔 Events

The integration fires an event whenever the displayed artwork changes (upload or rotation), so you can build automations off it:

```yaml
trigger:
  - platform: event
    event_type: samsung_frame_art_director_art_changed
# event.data: { host: "<tv ip>", content_id: "<id or path>" }
```

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---|---|
| Art uploads stall or fail | Ensure the TV is paired. Try turning on manually and watching for permission popups. |
| The TV repeatedly asks to allow Home Assistant | Update the integration, restart HA, and confirm the TV's **Device Connection Manager → Access Notification** setting is **First Time Only**. A normal restart reuses the saved token and must not show a prompt; a single new prompt is expected only when HA starts reauthentication for an explicitly rejected/expired token. |
| "No … API key" warning | Select a provider and add its API key under **Settings → Devices & services → Integrations → Samsung Frame Art Director → Configure**. |
| "Local file missing" warnings during rotation | Run **Purge Database** then **Sync Library** to clean up stale entries. |
| Gallery shows no images | Ensure images exist in `/media/frame/library/` and run **Sync Library**. |
| Rate limit (429) during inbox processing | Provider tiers have request limits. Wait a few minutes and try again. |

Check HA logs filtered by `samsung_frame_art_director` for detailed error messages.

> [!NOTE]
> **Art‑Mode settings (brightness, color temperature, motion, auto‑brightness) show `unknown` / don't apply.**
> These use the Art Mode API in `samsungtvws` ≥ 3.0.5 (installed automatically). They're **enabled by default**; if your TV or firmware rejects a particular setting, you can turn them off via the integration's options → *Advanced* → **Show Art‑Mode setting entities** (then reload). Check the installed library version in the logs at startup (`samsungtvws package version: …`).

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!
Feel free to check the [issues page](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/issues).

> **Working on the code?** See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup and checks. Read [`ARCHITECTURE.md`](ARCHITECTURE.md) before changing the TV API fallback logic; it explains the module layout, SQLite data model, key control flows, and design constraints.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

> [!WARNING]
> **TV Model Compatibility**
> Samsung's internal Art Mode APIs vary significantly between different models and production years. This integration has been primarily developed, tested, and confirmed working on model **Samsung The Frame Q65LS03DAU**. Your mileage may vary on older or newer models.

*Disclaimer: Not affiliated with Samsung. Uses the internal WebSockets API of Frame TVs.*
