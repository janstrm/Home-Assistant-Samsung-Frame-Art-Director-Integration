"""Regression tests for startup authentication and Art client lifecycle."""

import asyncio
import logging
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_art_director import (
    _enable_verbose_logging,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.samsung_frame_art_director.api import (
    AuthenticationRejectedError,
    DeviceUnavailableError,
    SamsungFrameClient,
)
from custom_components.samsung_frame_art_director.const import DOMAIN


def _fake_module(tv_type):
    return SimpleNamespace(SamsungTVWS=tv_type)


async def test_domain_setup_registers_shared_interfaces(hass):
    """The domain owns shared HTTP and action interfaces."""
    hass.http = MagicMock()

    assert await async_setup(hass, {})

    hass.http.register_view.assert_called_once()
    for action in (
        "set_artmode",
        "upload_art",
        "art_diagnostics",
        "rotate_art_now",
        "cleanup_storage",
        "process_inbox",
        "sync_library",
        "purge_database",
        "toggle_favorite",
        "delete_art",
        "rotate_favorites",
        "change_gallery_page",
    ):
        assert hass.services.has_service(DOMAIN, action), action


async def test_domain_setup_registers_websocket_interface(hass):
    """The gallery WebSocket command is owned by domain setup."""
    hass.http = MagicMock()

    with patch(
        "homeassistant.components.websocket_api.async_register_command"
    ) as register_command:
        assert await async_setup(hass, {})

    register_command.assert_called_once()


async def test_startup_reuses_saved_token_without_token_file_pairing(
    hass,
    tmp_path,
):
    """A normal HA restart validates the existing identity without re-pairing."""
    constructor_calls = []
    art_clients = []
    tv_clients = []

    class FakeArt:
        token = "SAVED"

        def __init__(self):
            self.close_calls = 0
            art_clients.append(self)

        def open(self):
            return object()

        def supported(self):
            raise AssertionError("REST support probing is not authentication")

        def close(self):
            self.close_calls += 1

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, **kwargs):
            constructor_calls.append(kwargs)
            self.close_calls = 0
            self._art = FakeArt()
            tv_clients.append(self)

        def art(self):
            return self._art

        def rest_device_info(self):
            return {"device": {"duid": "uuid:frame"}}

        def close(self):
            self.close_calls += 1

    obsolete_pairing_file = tmp_path / "pairing-token.txt"
    obsolete_pairing_file.write_text("SAVED", encoding="utf-8")
    client = SamsungFrameClient(
        hass,
        "frame.local",
        token="SAVED",
        token_file_path=str(obsolete_pairing_file),
        port=8002,
    )

    with patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}):
        await client.async_connect_and_pair()

    assert client.is_connected is True
    assert client.duid == "uuid:frame"
    assert constructor_calls == [
        {
            "port": 8002,
            "token": "SAVED",
            "name": "Home Assistant Art Director",
            "timeout": 10,
        }
    ]
    assert "token_file" not in constructor_calls[0]
    assert len(art_clients) == len(tv_clients) == 1
    assert art_clients[0].close_calls == 1
    assert tv_clients[0].close_calls == 1
    assert obsolete_pairing_file.exists() is False


async def test_offline_tv_is_a_transient_setup_failure(hass):
    """An unreachable TV must not appear connected or trigger reauthentication."""

    class FakeTV:
        def __init__(self, *_args, **_kwargs):
            raise OSError("host unreachable")

    client = SamsungFrameClient(hass, "frame.local", token="SAVED", port=8002)

    with (
        patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}),
        pytest.raises(DeviceUnavailableError),
    ):
        await client.async_connect_and_pair()

    assert client.is_connected is False


async def test_rejected_saved_token_is_an_auth_failure(hass):
    """Only an explicit unauthorized response should start HA reauthentication."""

    class UnauthorizedError(Exception):
        pass

    class FakeArt:
        def open(self):
            raise UnauthorizedError("rejected")

        def close(self):
            return None

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    client = SamsungFrameClient(hass, "frame.local", token="SAVED", port=8002)

    with (
        patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}),
        pytest.raises(AuthenticationRejectedError),
    ):
        await client.async_connect_and_pair()

    assert client.is_connected is False


async def test_rest_duid_does_not_replace_authenticated_validation(hass):
    """Public REST identity data alone is not proof that the token works."""

    class ConnectionFailure(Exception):
        pass

    class FakeArt:
        def open(self):
            raise ConnectionFailure("art channel failed")

        def close(self):
            return None

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def rest_device_info(self):
            return {"device": {"duid": "uuid:public-rest-only"}}

        def close(self):
            return None

    client = SamsungFrameClient(hass, "frame.local", token="SAVED", port=8002)

    with (
        patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}),
        pytest.raises(DeviceUnavailableError),
    ):
        await client.async_connect_and_pair()

    assert client.is_connected is False
    assert client.duid is None


async def test_timed_out_port_finishes_before_fallback_starts(hass):
    """A non-cancellable sync worker is drained before trying another port."""
    events = []

    class FakeArt:
        def __init__(self, port):
            self._port = port

        def open(self):
            if self._port == 8002:
                events.append("first-start")
                time.sleep(0.05)
                events.append("first-end")
            return object()

        def close(self):
            return None

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, port, **_kwargs):
            self._port = port
            if port == 8001:
                events.append("fallback-start")
            self._art = FakeArt(port)

        def art(self):
            return self._art

        def rest_device_info(self):
            return {"device": {"duid": f"uuid:{self._port}"}}

        def close(self):
            return None

    client = SamsungFrameClient(hass, "frame.local", token="SAVED")

    with (
        patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}),
        patch(
            "custom_components.samsung_frame_art_director.api.CONNECTION_ATTEMPT_TIMEOUT_SECONDS",
            0.01,
        ),
    ):
        await client.async_connect_and_pair()

    assert events == ["first-start", "first-end", "fallback-start"]
    assert client.duid == "uuid:8001"


async def test_timed_out_art_call_holds_serialization_until_worker_finishes(hass):
    """A timed-out Art worker cannot overlap the next Art operation."""
    events = []
    call_count = 0
    socket_timeouts = []

    class FakeArt:
        def get_brightness(self):
            nonlocal call_count
            call_count += 1
            current = call_count
            events.append(f"start-{current}")
            if current == 1:
                time.sleep(0.05)
            events.append(f"end-{current}")
            return current

        def close(self):
            return None

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, **kwargs):
            socket_timeouts.append(kwargs.get("timeout"))
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    client = SamsungFrameClient(hass, "frame.local", token="SAVED")

    with (
        patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}),
        patch(
            "custom_components.samsung_frame_art_director.api.ART_OPERATION_TIMEOUT_SECONDS",
            0.01,
        ),
    ):
        first = asyncio.create_task(client.async_get_brightness())
        await asyncio.sleep(0)
        second = asyncio.create_task(client.async_get_brightness())
        assert await first is None
        assert await second == 2

    assert events == ["start-1", "end-1", "start-2", "end-2"]
    assert socket_timeouts == [0.01, 0.01]


def test_shared_close_helper_closes_each_object_once(hass):
    """Defensive identity de-duplication prevents double-close side effects."""

    class SameClient:
        token = "SAVED"

        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    client = SamsungFrameClient(hass, "frame.local", token="SAVED")
    shared = SameClient()

    client._close_art_connection(shared, shared)

    assert shared.close_calls == 1


async def test_status_poll_closes_art_and_parent_exactly_once(hass):
    """Coordinator polling must not leave its short-lived Art channel open."""
    art_clients = []
    tv_clients = []

    class FakeArt:
        token = "SAVED"

        def __init__(self):
            self.close_calls = 0
            art_clients.append(self)

        def get_artmode(self):
            return "on"

        def close(self):
            self.close_calls += 1

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, **_kwargs):
            self.close_calls = 0
            self._art = FakeArt()
            tv_clients.append(self)

        def art(self):
            return self._art

        def close(self):
            self.close_calls += 1

    client = SamsungFrameClient(hass, "frame.local", token="SAVED")

    with patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}):
        assert await client.async_get_artmode_status() == "on"

    assert len(art_clients) == len(tv_clients) == 1
    assert art_clients[0].close_calls == 1
    assert tv_clients[0].close_calls == 1


async def test_preview_reuses_one_art_child_and_closes_it(hass):
    """Thumbnail fallbacks stay on the same Art child for one parent client."""
    art_calls = 0
    art_client = None
    tv_client = None

    class FakeArt:
        token = "SAVED"

        def __init__(self):
            self.close_calls = 0

        def supported(self):
            return True

        def get_current(self):
            return {"content_id": "MY-PREVIEW"}

        def get_photo(self, _content_id):
            return b"preview"

        def close(self):
            self.close_calls += 1

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, **_kwargs):
            nonlocal art_client, tv_client
            self.close_calls = 0
            self._art = FakeArt()
            art_client = self._art
            tv_client = self

        def art(self):
            nonlocal art_calls
            art_calls += 1
            return self._art

        def close(self):
            self.close_calls += 1

    client = SamsungFrameClient(hass, "frame.local", token="SAVED")

    with patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}):
        result = await client.async_get_current_art()

    assert result == {"content_id": "MY-PREVIEW", "image": b"preview"}
    assert art_calls == 1
    assert art_client.close_calls == 1
    assert tv_client.close_calls == 1


async def test_state_poll_and_setting_call_share_one_serialization_lock(hass):
    """Different public Art operations cannot run concurrently."""
    events = []
    state_started = asyncio.Event()
    release_state = asyncio.Event()

    class FakeArt:
        token = "SAVED"

        def get_artmode(self):
            events.append("state-start")
            hass.loop.call_soon_threadsafe(state_started.set)
            while not release_state.is_set():
                time.sleep(0.005)
            events.append("state-end")
            return "on"

        def get_current(self):
            return {"content_id": "MY-CURRENT"}

        def get_brightness(self):
            events.append("brightness")
            return 5

        def close(self):
            return None

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    client = SamsungFrameClient(hass, "frame.local", token="SAVED")

    with patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}):
        state_task = asyncio.create_task(client.async_get_state())
        await state_started.wait()
        brightness_task = asyncio.create_task(client.async_get_brightness())
        await asyncio.sleep(0.02)
        assert events == ["state-start"]
        release_state.set()
        assert await state_task == {
            "status": "on",
            "content_id": "MY-CURRENT",
        }
        assert await brightness_task == 5

    assert events == ["state-start", "state-end", "brightness"]


async def test_cleanup_revalidates_current_art_before_deleting(hass, tmp_path):
    """A selection between cleanup scan and delete must be preserved."""
    current_ids = iter(["MY-OTHER", "MY-DELETE"])
    deleted_ids = []

    class FakeArt:
        token = "SAVED"

        def get_current(self):
            return {"content_id": next(current_ids)}

        def available(self):
            return [{"content_id": "MY-DELETE"}]

        def delete_list(self, content_ids):
            deleted_ids.extend(content_ids)

        def close(self):
            return None

    class FakeTV:
        token = "SAVED"

        def __init__(self, *_args, **_kwargs):
            self._art = FakeArt()

        def art(self):
            return self._art

        def close(self):
            return None

    client = SamsungFrameClient(hass, "frame.local", token="SAVED")
    client.set_db_path(str(tmp_path / "art.db"))
    await client.async_track_art(
        "MY-DELETE",
        source_file="/media/frame/library/delete.jpg",
    )

    with patch.dict(sys.modules, {"samsungtvws": _fake_module(FakeTV)}):
        summary = await client.async_cleanup_storage(
            max_items=0,
            preserve_current=True,
        )

    assert deleted_ids == []
    assert summary["deleted"] == []
    assert summary["to_delete"] == []
    assert "MY-DELETE" in summary["skipped_current"]


async def test_setup_stops_before_tv_connection_when_database_init_fails(hass):
    """A broken library database is a visible, retryable setup failure."""
    hass.http = MagicMock()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "port": 8002, "token": "SAVED"},
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.host = "frame.local"
    client.token = "SAVED"
    client.async_initialize_database = AsyncMock(
        side_effect=OSError("database is read-only")
    )
    client.async_connect_and_pair = AsyncMock()

    with (
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            return_value=client,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.samsung_frame_art_director._reload_slideshow_timer",
            AsyncMock(),
        ),
        patch("homeassistant.components.websocket_api.async_register_command"),
        pytest.raises(ConfigEntryNotReady, match="database is read-only"),
    ):
        await async_setup_entry(hass, entry)

    client.async_connect_and_pair.assert_not_awaited()


async def test_config_entry_owns_and_cleans_up_its_runtime(hass):
    """A loaded entry retains and disconnects its own Frame client."""
    hass.http = MagicMock()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "port": 8002, "token": "SAVED"},
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.host = "frame.local"
    client.token = "SAVED"
    client.async_initialize_database = AsyncMock()
    client.async_connect_and_pair = AsyncMock()
    client.async_disconnect = AsyncMock()

    with (
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            return_value=client,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.samsung_frame_art_director._reload_slideshow_timer",
            AsyncMock(),
        ),
        patch("homeassistant.components.websocket_api.async_register_command"),
    ):
        assert await async_setup_entry(hass, entry)
        assert entry.runtime_data.client is client
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
        assert await async_unload_entry(hass, entry)

    client.async_disconnect.assert_awaited_once_with()


async def test_action_rejects_an_unknown_target(hass):
    """An action never reports success for an unresolved Frame entity."""
    hass.http = MagicMock()
    assert await async_setup(hass, {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "port": 8002, "token": "SAVED"},
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.host = "frame.local"
    client.token = "SAVED"
    client.async_initialize_database = AsyncMock()
    client.async_connect_and_pair = AsyncMock()
    client.async_set_artmode = AsyncMock()

    with (
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            return_value=client,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.samsung_frame_art_director._reload_slideshow_timer",
            AsyncMock(),
        ),
        patch("homeassistant.components.websocket_api.async_register_command"),
    ):
        assert await async_setup_entry(hass, entry)

    with pytest.raises(
        ServiceValidationError,
        match="not a loaded Samsung Frame Art Director entity",
    ):
        await hass.services.async_call(
            DOMAIN,
            "set_artmode",
            {
                "enabled": True,
                "entity_id": "media_player.unknown_frame",
            },
            blocking=True,
        )

    client.async_set_artmode.assert_not_awaited()


@pytest.mark.parametrize(
    ("client_error", "setup_error"),
    [
        (AuthenticationRejectedError("rejected"), ConfigEntryAuthFailed),
        (DeviceUnavailableError("offline"), ConfigEntryNotReady),
    ],
)
async def test_setup_maps_auth_and_reachability_failures_separately(
    hass,
    client_error,
    setup_error,
):
    """HA starts reauth only for rejection and retries an offline device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "frame.local", "port": 8002, "token": "SAVED"},
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_initialize_database = AsyncMock()
    client.async_connect_and_pair = AsyncMock(side_effect=client_error)

    with (
        patch(
            "custom_components.samsung_frame_art_director.api.SamsungFrameClient",
            return_value=client,
        ),
        pytest.raises(setup_error),
    ):
        await async_setup_entry(hass, entry)


def test_verbose_logging_suppresses_library_token_messages():
    """The dependency module known to print tokens never runs below WARNING."""
    connection_logger = logging.getLogger("samsungtvws.connection")
    previous_level = connection_logger.level
    try:
        _enable_verbose_logging()
        assert connection_logger.getEffectiveLevel() >= logging.WARNING
    finally:
        connection_logger.setLevel(previous_level)
