<p align="center">
  <img src="https://raw.githubusercontent.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/main/docs/logo.png" alt="Samsung Frame Art Director" width="200" style="border-radius:20px"/>
</p>

# 🖼️ Samsung Frame Art Director
> **A Custom Integration for Home Assistant to control Samsung Frame TV Art Mode.**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=janstrm&repository=Home-Assistant-Samsung-Frame-Art-Director-Integration&category=integration)

Control your Samsung Frame TV's Art Mode directly from Home Assistant. This custom component uses the async Art API to provide reliable on/off toggles, local image uploads with automatic resizing, automated rotation, and television storage management. It also features an optional Gemini AI integration that auto-tags dropped images, and exposes a Gallery Sensor to easily build dashboards.

---

## 📋 Prerequisites

- **Home Assistant** (Core, Supervised, or OS)
- **HACS** (Home Assistant Community Store) installed.
- A **Samsung Frame TV** connected to the same local network.
- (Optional) A **Google Gemini API Key** to enable AI Auto-Tagging capabilities.

---

## ✨ Capabilities

- **State Verification:** Toggles Art Mode ON/OFF and verifies the state to ensure the screen displays art rather than just being powered down.
- **Local Uploads:** Upload local images directly to the TV. Images are programmatically center-cropped and resized to 3840×2160 pixels before upload.
- **Gemini AI Auto-Tagging:** Monitors an "inbox" folder; when images are detected, Google's Gemini AI analyzes, tags, and describes the art before cataloging it to the local library.
- **Gallery Sensor:** Exposes a database of your local art, allowing you to build dashboard views with the provided example YAML.
- **Auto-Rotation:** Rotates art from local storage or limits selection based on assigned tags, favorites, and filters.
- **Favorites:** Mark individual artworks as favorites. Filter the gallery or rotation to only use your favorite pieces.
- **Storage Management:** Detects and deletes orphaned or un-favorited artworks from the TV memory to manage limited storage capacity.

---

## 🚀 Installation

### Method 1: HACS (Recommended)
1. Open HACS in Home Assistant.
2. Go to **Integrations** -> Click the 3-dots in the top right -> **Custom repositories**.
3. Add `https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration` as an **Integration**.
4. Click Install and restart Home Assistant.

### Method 2: Manual
1. Download this repository.
2. Copy the `custom_components/samsung_frame_art_director/` folder into your Home Assistant `/config/custom_components/` directory.
3. Restart Home Assistant.

---

## ⚙️ Configuration

Add the integration from **Settings → Devices & Services → Add Integration** and search for "Samsung Frame Art Director".

### Initial Setup
- You will be asked for the TV's IP address and a Name.
- Follow the prompt on your TV to "Allow" the connection.

### Options Flow (Configure)
Once installed, click **Configure** on the integration page to access advanced settings:
- **Gemini API Key:** Required for AI Auto-Tagging (Process Inbox & Sync Library).
- **Slideshow Settings:** Enable rotation, set interval (minutes), and select source type (Library / Folder / Tags).
- **Matte:** Enable/disable a polar matte border around displayed art.
- **Wake-on-LAN:** Enter the TV's MAC address to wake it before sending commands.
- **Cleanup Settings:** Define max items on TV, max storage age, and whether to preserve favorites.

---

## 📂 Folder Structure

The integration uses two folders on your HA filesystem:

| Folder | Purpose |
|---|---|
| `/media/frame/inbox` | Drop new images here. **Process Inbox** will analyze, tag, and move them to the library. |
| `/media/frame/library` | Permanent storage for processed images. Used by rotation and the gallery sensor. |

### Workflow
1. Drop images into `/media/frame/inbox/`
2. Run **Process Inbox** → Gemini AI tags each image, then moves it to `/media/frame/library/`
3. Images appear in the Gallery sensor and are available for rotation
4. If you add images directly to `/media/frame/library/`, run **Sync Library** to tag and register them

---

## 🖥️ Dashboard Example (UI)

We provide a **ready-to-use Dashboard YAML** that combines the TV controls and the AI Art Gallery into a beautiful 3-column frontend view.

You can find the code here: [`examples/dashboard.yaml`](examples/dashboard.yaml)

To use the AI Art Gallery with popups, you will need these HACS frontend plugins:
1. **[auto-entities](https://github.com/thomasloven/lovelace-auto-entities)** (For the dynamic image gallery grid)
2. **[browser_mod](https://github.com/thomasloven/hass-browser_mod)** (For clicking an image to open the Push/Favorite/Delete popup)
3. **[card-mod](https://github.com/thomasloven/lovelace-card-mod)** *(Optional)* (For visual enhancements like favorite indicators)

Simply create a new Dashboard View in Home Assistant, click **Edit (Raw Configuration)**, and paste the contents of the example file! Home Assistant will automatically arrange the 3 columns on wide screens.

---

## 🎮 Services

Domain: `samsung_frame_art_director`

### Core Services

#### set_artmode
Toggle Art Mode on or off.
```yaml
service: samsung_frame_art_director.set_artmode
target:
  entity_id: media_player.samsung_frame
data:
  enabled: true
```

#### upload_art
Upload and immediately display an image from your HA filesystem.
```yaml
service: samsung_frame_art_director.upload_art
target:
  entity_id: media_player.samsung_frame
data:
  path: /media/frame/library/example.jpg
```
*(Paths must reside in `/media` or `/config` for security).*

#### rotate_art_now
Force an immediate rotation of the displayed art. Picks a random image from the library (optionally filtered by tags). Automatically retries if a selected image no longer exists on disk.
```yaml
service: samsung_frame_art_director.rotate_art_now
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
service: samsung_frame_art_director.rotate_favorites
target:
  entity_id: media_player.samsung_frame
```

### AI & Library Services

#### process_inbox
Scan `/media/frame/inbox`, analyze each image with Gemini AI, move to `/media/frame/library`, and register in the database with tags.
```yaml
service: samsung_frame_art_director.process_inbox
```
> **Note:** Requires a Gemini API key in the integration options. If rate-limited (HTTP 429), processing pauses and logs how many images were completed.

#### sync_library
Scan `/media/frame/library` for any untracked images (e.g. manually added files) and register them in the database with AI tags.
```yaml
service: samsung_frame_art_director.sync_library
```

#### purge_database
Wipe the local SQLite database (art history, AI tags, favorites). **Does NOT delete image files** from `/media/frame/library/`.
```yaml
service: samsung_frame_art_director.purge_database
```
> **Tip:** After purging, run **Sync Library** to re-scan and re-tag your existing images.

### Gallery Management Services

#### toggle_favorite
Toggle the favorite status of an artwork in the library database.
```yaml
service: samsung_frame_art_director.toggle_favorite
data:
  content_id: "MY-C0002_xxxxxxxx"
```

#### delete_art
Delete an artwork from the library database.
```yaml
service: samsung_frame_art_director.delete_art
data:
  content_id: "MY-C0002_xxxxxxxx"
```

#### cleanup_storage
Remove non-favorite artworks from the **TV's internal storage** to free up space.
```yaml
service: samsung_frame_art_director.cleanup_storage
target:
  entity_id: media_player.samsung_frame
data:
  max_items: 50                  # Optional: keep at most N items
  max_age_days: 30               # Optional: delete items older than N days
  preserve_current: true         # Optional: don't delete the currently displayed artwork
  only_integration_managed: true # Optional: only delete items tracked by this integration
  dry_run: false                 # Optional: preview what would be deleted without actually deleting
```

### Diagnostics

#### art_diagnostics
Log Art Mode support status, current artwork, and a sample of available content IDs (useful for debugging).
```yaml
service: samsung_frame_art_director.art_diagnostics
target:
  entity_id: media_player.samsung_frame
```

---

## 📊 Entities

When configured, the integration creates the following entities (where `samsung_frame` is your configured device name):

### Media Player
| Entity | Description |
|---|---|
| `media_player.samsung_frame` | Main control entity. State reflects TV power. Attributes include `art_mode_status`. |

### Image
| Entity | Description |
|---|---|
| `image.samsung_frame_art_preview` | Live preview of the currently displayed artwork on the Frame TV. |

### Switches
| Entity | Description |
|---|---|
| `switch.samsung_frame_slideshow_enabled` | Enable/disable automatic art rotation. |
| `switch.samsung_frame_matte_enabled` | Enable/disable the polar matte border around displayed art. |
| `switch.samsung_frame_gallery_favorites_only` | Restrict the gallery and rotation to only favorited images. |

### Select Entities
| Entity | Description |
|---|---|
| `select.samsung_frame_slideshow_source` | Choose rotation source: `Library`, `Folder`, or `Tags`. |
| `select.samsung_frame_slideshow_interval` | Quick-pick rotation interval (1, 2, 5, 10, 15, 30, 60, 120, 240 min). |

### Number Entities
| Entity | Description |
|---|---|
| `number.samsung_frame_slideshow_interval` | Custom rotation interval in minutes (0–1440). |

### Text Entities
| Entity | Description |
|---|---|
| `text.samsung_frame_slideshow_filter` | Free-text filter for tags or folder path used by rotation. |

### Sensors
| Entity | Description |
|---|---|
| `sensor.samsung_frame_art_library` | Reports total tracked artworks. Attributes include the full `items` list for dashboard gallery rendering. |

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---|---|
| Art uploads stall or fail | Ensure the TV is paired. Try turning on manually and watching for permission popups. |
| "No Gemini API key" warning | Add your API key in **Settings → Devices → Samsung Frame Art Director → Configure**. |
| "Local file missing" warnings during rotation | Run **Reset Database** then **Sync Library** to clean up stale entries. |
| Gallery shows no images | Ensure images exist in `/media/frame/library/` and run **Sync Library**. |
| Rate limit (429) during inbox processing | Gemini free tier has request limits. Wait a few minutes and try again. |

Check HA logs filtered by `samsung_frame_art_director` for detailed error messages.

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!
Feel free to check the [issues page](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/issues).

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
