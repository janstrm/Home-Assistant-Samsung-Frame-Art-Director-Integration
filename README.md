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

> [!NOTE] 
> **Custom Integration vs. Add-on**
> This project is a Home Assistant **Custom Integration** (Custom Component), *not* an Add-on. 
> - **Add-ons** (like AdGuard or Node-RED) are separate Docker containers, which is why they list supported architectures (aarch64, amd64, etc.).
> - **Integrations** run natively inside Python in HA Core. Therefore, this project naturally supports **all HA architectures** (Raspberry Pi, x86, NAS, etc.) out-of-the-box!

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
- **Gallery Sensor:** Exposes a paginated database of your local art via WebSockets, allowing you to build dashboard views.
- **Auto-Rotation:** Rotates art from local storage or limits selection based on assigned tags and filters.
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

Add the integration from **Settings → Devices & Services → Add Integration** and search for “Samsung Frame Art Director”.

### Initial Setup
- You will be asked for the TV's IP address and a Name.
- Follow the prompt on your TV to "Allow" the connection.

### Options Flow (Configure)
Once installed, click **Configure** on the integration page to access advanced settings:
- **Gemini API Key:** Required for AI Auto-Tagging.
- **Slideshow Settings:** Enable rotation, set interval (minutes), and filter by tags.
- **Wake-on-LAN:** Enter the TV's MAC address to wake it before sending commands.
- **Cleanup Settings:** Define max items on TV, max storage age, and whether to preserve favorites.

---

## 🎮 Services

Domain: `samsung_frame_art_director`

### 1. set_artmode
Toggle Art Mode on or off.
```yaml
service: samsung_frame_art_director.set_artmode
target:
  entity_id: media_player.samsung_frame
data:
  enabled: true
```

### 2. upload_art
Upload and immediately display an image from your HA filesystem.
```yaml
service: samsung_frame_art_director.upload_art
target:
  entity_id: media_player.samsung_frame
data:
  path: /media/frame/library/example.jpg
```
*(Paths must reside in `/media` or `/config` for security).*

### 3. rotate_art_now
Force an immediate rotation of the displayed art.
```yaml
service: samsung_frame_art_director.rotate_art_now
target:
  entity_id: media_player.samsung_frame
data:
  source: library       # library | folder
  tags: "nature, ocean" # Optional: only rotate images matching these tags
```

### 4. process_inbox
Trigger the Gemini AI to scan the `/media/frame/inbox` folder, analyze new images, tag them, and add them to your local library database.
```yaml
service: samsung_frame_art_director.process_inbox
target:
  entity_id: media_player.samsung_frame
```

### 5. cleanup_storage
Safely remove non‑favorite artworks from the TV to free up space.
```yaml
service: samsung_frame_art_director.cleanup_storage
target:
  entity_id: media_player.samsung_frame
```

---

## 📊 Useful Entities

When configured, the integration provides several entities:
- `media_player.samsung_frame_art_director`: The main control entity (turn ON to enter Art Mode).
- `sensor.samsung_frame_art_director_library`: Reports the total number of processed artworks and provides paginated views via WebSocket.
- `switch.samsung_frame_art_director_slideshow`: Easily pause/resume the background rotator.
- `switch.samsung_frame_art_director_gallery_favorites_only`: Toggle this to restrict the dashboard gallery or rotation strictly to favorited images.

---

## 🛠️ Troubleshooting

If Art Uploads stall or fail:
- Ensure the TV is securely paired. Try turning the TV on manually and checking for popups.
- Check standard HA Logs for `samsung_frame_art_director` errors.
- Ensure your Image paths actually exist and are accessible within Home Assistant's `/media` directory.

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
