"""Test exactly one Art profile using an existing HA credential, without writes."""

import argparse
import importlib.util
import json
import logging
from pathlib import Path

PROFILES = {
    "8002-token": (8002, "saved_remote_token"),
    "8002-tokenless": (8002, "tokenless"),
    "8001-tokenless": (8001, "tokenless"),
}


def load_identity(config: Path, host: str) -> str:
    """Read only the selected entry; never print or modify config-entry data."""
    data = json.loads((config / ".storage" / "core.config_entries").read_text())
    entries = [
        entry for entry in data["data"]["entries"] if entry["domain"] == "samsung_frame_art_director" and entry["data"].get("host") == host
    ]
    if len(entries) != 1 or not entries[0]["data"].get("token"):
        raise ValueError("A unique existing entry with a Remote token is required")
    return entries[0]["data"]["token"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/config"))
    parser.add_argument("--host", required=True, help="Exact host/IP from the integration entry")
    parser.add_argument("--profile", choices=PROFILES, default="8002-token")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    logging.getLogger("samsungtvws").setLevel(logging.CRITICAL)
    logging.getLogger("websocket").setLevel(logging.CRITICAL)
    # Load the same adapter as the integration without importing Home Assistant.
    source = Path(__file__).resolve().parents[1] / "custom_components/samsung_frame_art_director/art_connection.py"
    spec = importlib.util.spec_from_file_location("art_profile_probe", source)
    adapter = importlib.util.module_from_spec(spec)
    art = None
    try:
        spec.loader.exec_module(adapter)
        token = load_identity(args.config, args.host)
        port, mode = PROFILES[args.profile]
        art = adapter.ManagedArt(
            args.host,
            port=port,
            token=token if mode == "saved_remote_token" else None,
            name="Home Assistant Art Director",
            timeout=10,
        )
        art.auth_mode = mode
        print(f"Testing only {args.profile}. Watch the TV for an approval prompt.", flush=True)
        art.open()
        status = art.get_artmode()
        print("Handshake ready; Art status request completed.")
        print(f"Art mode: {status if status in ('on', 'off') else 'unknown'}")
        print("Record whether a prompt appeared. Repeat this same command before comparing another profile.")
        return 0
    except Exception as err:  # noqa: BLE001
        # Do not echo exception bodies, connection URLs or credential-bearing frames.
        print(f"Probe failed ({type(err).__name__}). No configuration was written.")
        return 1
    finally:
        if art is not None:
            art.close()


if __name__ == "__main__":
    raise SystemExit(main())
