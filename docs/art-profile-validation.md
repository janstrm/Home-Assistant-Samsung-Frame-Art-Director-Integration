# Art connection profile beta validation

Version: **1.11.4b1**, related to GitHub issue #7. Hardware validation is pending.

## Short test for Shane

Use the official [GitHub pre-release v1.11.4b1](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/releases/tag/v1.11.4b1).

1. In HACS, open **Samsung Frame Art Director**, choose **Redownload**, and
   select **v1.11.4b1** in the version selector (some versions label this
   **Need a different version?**). Keep the existing integration and TV approval.
2. Restart Home Assistant with the TV awake. Check whether Art control works
   and whether an approval prompt appears. If you approve a prompt, mention it.
3. Restart Home Assistant once more with the TV still awake and check again.

Please report only **Art control works: yes/no**, and **approval prompt on
first restart: yes/no; on second restart: yes/no**.

No terminal commands, scripts, token files or extra logs are needed for this
first test. To roll back, select **v1.11.3** in HACS and restart Home Assistant.
If the beta is missing, refresh the repository information. For beta update
notifications, see the [official HACS pre-release switch documentation](https://www.hacs.xyz/docs/use/entities/switch/).

This beta starts with token-bearing Art/8002 when no working Art profile has
been saved. A saved profile still comes first; tokenless fallback remains
available. This is an integration test with fallback, not an isolated protocol
experiment.

## Automated validation (2026-09-08)

- HA 2024.7 / Python 3.12.13: **263 passed**, 72.36% integration coverage.
- HA 2026.8 / Python 3.14.2: **263 passed**, 72.36% integration coverage.
- Ruff, JSON parsing, Python compilation and diff whitespace checks passed.
- Both suites ran in Linux/WSL using the committed dependency locks. The
  minimum-stack runner registered the WebP MIME type explicitly because the
  local system MIME database did not contain it. No tests were skipped.
- HACS/hassfest container validation was not run locally: Docker's daemon was
  unavailable. Run the repository CI checks before publishing a release.

The integration learns an Art port and authentication mode independently from the
Remote connection. It saves the profile after successful startup, uses it for
all Art operations, and tries it first after a restart or reload. Existing
entries need no migration. No profile is inferred from the TV model or firmware.

The three supported profiles are WSS/8002 without a token, WSS/8002 with the
current Remote token, and WS/8001 without a token. A failed saved profile can
fall back on a timeout or explicit Art rejection. A refused port skips any
remaining authentication modes on that port and tries the other port. Other transport/protocol
failures, including clientDisconnect, stop that setup attempt and use HA's
normal retry backoff. Art failures never invalidate Remote credentials.

## Optional isolated test for maintainers

Maintainers can test **8002 with the saved Remote token** in isolation. This is
not part of Shane's initial test above. The helper reads
the existing credential locally and does not write configuration, rotate
artwork, run a Remote pairing handshake, or try another profile automatically.

1. Extract the beta bundle on a computer/container that can reach the TV and
   read the Home Assistant configuration directory. Keep the TV in the same
   awake state for each comparison. Temporarily disable this integration in HA
   so its polling/retries cannot overlap the probe; retain the existing entry
   and allowed-device entry on the TV.
2. Use a Python environment with `samsungtvws[async,encrypted]==3.0.5` installed.
   Run from the bundle root, replacing the configuration path and TV IP:

   ```sh
   python scripts/probe_art_connection.py --config /config --host 192.168.1.241 --profile 8002-token
   ```

3. Watch the TV and record whether an approval prompt appears, whether it was
   accepted, the printed handshake phases, and whether the Art status request
   completes. A success after accepting a prompt is not yet a prompt-free result.
4. Run the **same** command again twice. If needed, compare `8002-tokenless` and
   `8001-tokenless` in separate invocations after the previous command exits.
   Keep the client name and TV state unchanged. Do not share the credential or
   the `.storage/core.config_entries` file; the helper output contains no token.
5. Re-enable the integration after the probe. If testing the beta integration,
   install the included `custom_components/samsung_frame_art_director` folder
   using the normal manual-install procedure and restart HA. The beta uses
   automatic negotiation; the probe itself does not save its selected profile.

## Integration acceptance

On Shane's device and the previously working tokenless maintainer device:

- Verify the logged `Art connected port=... auth_mode=...` profile and that the
  same profile is used first after two HA restarts and two integration reloads.
- Verify Art status/preview polling, upload/select, and 30 minutes idle without
  repeated approval prompts. Record any prompt separately from command success.
- Check that Remote control still works and no new Remote reauthentication is
  requested solely because an Art attempt failed.

Do not call the issue resolved until both devices pass. The beta does not
silently discard clientDisconnect events; the suggested delayed-event race
remains unproven. Keep any long-lived-socket redesign separate from this change.
