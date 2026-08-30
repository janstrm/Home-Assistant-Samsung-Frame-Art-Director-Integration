<p align="center">
  <img src="https://raw.githubusercontent.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/main/custom_components/samsung_frame_art_director/icon.png" alt="Samsung Frame Art Director" width="160"/>
</p>

# Samsung Frame Art Director

Control Samsung The Frame Art Mode from Home Assistant. Upload images, rotate local artwork, browse a gallery, manage TV storage, and optionally generate image tags with Google Gemini, OpenAI, or Anthropic.

## Before you install

You need Home Assistant 2024.7 or newer, HACS, and a Samsung Frame TV on the same local network. Samsung changes its internal Art Mode API between models and firmware versions, so some controls may not work on every Frame.

## Install and connect

1. Select **Download** in HACS
2. Restart Home Assistant
3. Open **Settings → Devices & services**
4. Select **Add integration** and search for **Samsung Frame Art Director**
5. Enter the TV address and approve the connection prompt on the TV

Once setup finishes, try Art Mode from **Developer tools → Actions**:

```yaml
action: samsung_frame_art_director.set_artmode
target:
  entity_id: media_player.your_frame_tv
data:
  enabled: true
```

Replace `media_player.your_frame_tv` with the media-player entity created for your Frame.

## What you can do

- Upload local JPEG, PNG, or WebP files and trusted HTTP(S) image URLs
- Resize images to 3840 × 2160 with crop or fit behavior
- Rotate images by folder, tags, or favorites
- Browse and display your local art through Home Assistant Media
- Pair Samsung IP Control for explicit power-on, power-off, and reboot actions
- Clean up integration-managed images in the TV's internal storage
- Use the included [gallery dashboard](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/blob/main/examples/dashboard.yaml)

## Optional cloud image tagging

**Process Inbox** and **Sync Library** can send images to a cloud model for tags and descriptions. This feature is disabled until you select a provider and enter its API key under **Configure → AI Image Tagging**.

| Provider | Default model | Create an API key |
|---|---|---|
| Google Gemini | `gemini-2.5-flash` | [Google AI Studio](https://aistudio.google.com/app/apikey) |
| OpenAI | `gpt-4o` | [OpenAI API keys](https://platform.openai.com/api-keys) |
| Anthropic | `claude-haiku-4-5-20251001` | [Anthropic Console](https://console.anthropic.com/settings/keys) |

The full image leaves Home Assistant when you use cloud tagging. Your provider may charge for requests. Custom model IDs must support image input.

## Documentation and support

- Read the [complete setup and action reference](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration#readme)
- Use the [testing dashboard](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/blob/main/examples/testing_dashboard.yaml) to exercise common entities and actions
- Review [open issues](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/issues) or submit a bug report with your TV model, Home Assistant version, integration version, and filtered logs

This project is not affiliated with Samsung. It uses the TV's internal WebSocket and Art Mode APIs.
