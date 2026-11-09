"""A small RCON client for a vanilla Minecraft server.

    with Rcon(host, port, password) as r:
        r.cmd("list")        # or r("list")
"""
from __future__ import annotations

import socket
import struct


def _read(s: socket.socket, n: int) -> bytes:
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("rcon closed")
        b += c
    return b


class Rcon:
    """One persistent RCON connection. Connects on `with` or connect()."""

    def __init__(self, host: str = "127.0.0.1", port: int = 25575, password: str = "", timeout: float = 60):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self.s: socket.socket | None = None
        self.k = 1

    def connect(self):
        self.s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._send(3, self.password)
        if self._recv()[0] == -1:
            raise SystemExit("rcon auth failed")
        return self

    def __enter__(self):
        return self.connect()

    def __exit__(self, *a):
        self.close()

    def close(self) -> None:
        self.s.close()

    def _send(self, typ: int, body: str) -> int:
        self.k += 1
        b = body.encode() + b"\x00\x00"
        self.s.sendall(struct.pack("<iii", len(b) + 8, self.k, typ) + b)
        return self.k

    def _recv(self):
        n = struct.unpack("<i", _read(self.s, 4))[0]
        d = _read(self.s, n)
        return struct.unpack("<ii", d[:8])[0], d[8:-2].decode(errors="replace")

    def cmd(self, c: str) -> str:
        self._send(2, c)
        return self._recv()[1]

    __call__ = cmd

    def cmds(self, cs: list[str]) -> list[str]:
        """Run many commands in order."""
        # Vanilla's RCON reads one packet per socket read, so pipelining drops packets: send one at a time.
        return [self.cmd(c) for c in cs]
