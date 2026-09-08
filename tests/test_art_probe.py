"""The standalone diagnostic reads one identity and never sweeps profiles."""

import json
from types import SimpleNamespace

import pytest

from scripts import probe_art_connection as probe


def config_file(tmp_path):
    path = tmp_path / ".storage" / "core.config_entries"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "data": {
                    "entries": [
                        {"domain": "samsung_frame_art_director", "data": {"host": "frame.local", "token": "SECRET"}},
                        {"domain": "other", "data": {"host": "frame.local", "token": "OTHER-SECRET"}},
                    ]
                }
            }
        )
    )
    return path


def test_probe_reads_selected_entry_without_modification(tmp_path, capsys):
    path = config_file(tmp_path)
    before = path.read_bytes()
    assert probe.load_identity(tmp_path, "frame.local") == "SECRET"
    with pytest.raises(ValueError):
        probe.load_identity(tmp_path, "another-frame")
    assert path.read_bytes() == before
    assert not capsys.readouterr().out


@pytest.mark.parametrize("failed", [False, True])
def test_probe_runs_one_profile_and_sanitizes_errors(tmp_path, monkeypatch, capsys, failed):
    path = config_file(tmp_path)
    before = path.read_bytes()
    calls = []

    class Art:
        def __init__(self, host, **kwargs):
            calls.append(kwargs)

        def open(self):
            if failed:
                raise OSError("token=SECRET")

        def get_artmode(self):
            return "on"

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(
        probe.importlib.util,
        "spec_from_file_location",
        lambda *args: SimpleNamespace(
            loader=SimpleNamespace(exec_module=lambda module: None),
        ),
    )
    monkeypatch.setattr(probe.importlib.util, "module_from_spec", lambda spec: SimpleNamespace(ManagedArt=Art))
    monkeypatch.setattr("sys.argv", ["probe", "--config", str(tmp_path), "--host", "frame.local", "--profile", "8002-token"])
    assert probe.main() == int(failed)
    assert len(calls) == 2
    assert calls[0]["token"] == "SECRET"
    assert calls[0]["port"] == 8002
    assert calls[1] == "closed"
    assert "SECRET" not in capsys.readouterr().out
    assert path.read_bytes() == before
