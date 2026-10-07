"""Bound preview receive loops without changing shared SDK classes."""

from __future__ import annotations

from time import monotonic


def bound_preview_reads(art, timeout: float) -> None:
    """Apply one receive budget to this disposable Art child only.

    A socket timeout limits silence, not total time: unrelated TV events and
    trickling thumbnail bytes can otherwise keep the SDK's receive loops alive.
    Keep SDK request matching and decoding; bound their individual reads.
    Alternate clients without these hooks retain their existing socket timeout.
    """
    deadline = monotonic() + timeout

    def remaining() -> float:
        seconds = deadline - monotonic()
        if seconds <= 0:
            raise TimeoutError("Art preview receive deadline exceeded")
        return seconds

    receive = getattr(art, "_recv_frame", None)
    if callable(receive):
        def receive_frame():
            seconds = remaining()
            connection = getattr(art, "connection", None)
            if connection is not None:
                connection.settimeout(seconds)
            return receive()

        art._recv_frame = receive_frame

    open_socket = getattr(art, "_open_d2d_socket", None)
    if callable(open_socket):
        def open_d2d_socket(connection_info):
            art.timeout = remaining()
            return open_socket(connection_info)

        art._open_d2d_socket = open_d2d_socket

    receive_exact = getattr(art, "_recv_exact", None)
    if callable(receive_exact):
        class DeadlineSocket:
            def __init__(self, sock):
                self._socket = sock

            def recv(self, size):
                self._socket.settimeout(remaining())
                return self._socket.recv(size)

        def receive_bytes(sock, size):
            return receive_exact(DeadlineSocket(sock), size)

        art._recv_exact = receive_bytes
