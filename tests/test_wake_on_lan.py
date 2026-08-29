"""Tests for the self-contained Wake-on-LAN helper."""

import socket
from unittest.mock import MagicMock, call, patch

import pytest

from custom_components.samsung_frame_art_director import _send_magic_packet


def test_send_magic_packet_uses_global_and_directed_broadcasts():
    """The helper sends the packet to both standard ports on every target."""
    sock = MagicMock()
    sock.__enter__.return_value = sock

    with patch("socket.socket", return_value=sock):
        _send_magic_packet("AA:BB:CC:DD:EE:FF", ["192.168.68.255"])

    packet = bytes.fromhex("FF" * 6 + "AABBCCDDEEFF" * 16)
    sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    assert sock.sendto.call_args_list == [
        call(packet, ("255.255.255.255", 9)),
        call(packet, ("255.255.255.255", 7)),
        call(packet, ("192.168.68.255", 9)),
        call(packet, ("192.168.68.255", 7)),
    ]


def test_send_magic_packet_rejects_invalid_mac():
    """Malformed MAC addresses fail before opening a socket."""
    with (
        patch("socket.socket") as socket_factory,
        pytest.raises(ValueError, match="Invalid MAC address"),
    ):
        _send_magic_packet("not-a-mac")

    socket_factory.assert_not_called()


def test_send_magic_packet_raises_when_every_send_fails():
    """A total socket failure reaches the service layer for warning logging."""
    sock = MagicMock()
    sock.__enter__.return_value = sock
    sock.sendto.side_effect = OSError("network unreachable")

    with (
        patch("socket.socket", return_value=sock),
        pytest.raises(OSError, match="network unreachable"),
    ):
        _send_magic_packet("AA:BB:CC:DD:EE:FF")
