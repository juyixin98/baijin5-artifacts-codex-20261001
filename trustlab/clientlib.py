"""Client helper used by the demo and the tests (not part of the server).

Speaks the frame protocol from frames.py over a mutually-authenticated
TLS session. Handshake failures surface to the caller as ssl.SSLError;
the categorized cause is recorded server-side (GET /failures).
"""
from __future__ import annotations

import socket
import ssl

from . import frames


class ClientSession:
    def __init__(self, sock: ssl.SSLSocket, reader, hello: dict) -> None:
        self.sock = sock
        self._reader = reader
        self.hello = hello

    @property
    def connection_id(self) -> str:
        return self.hello["connection_id"]

    @property
    def identity(self) -> dict:
        return self.hello["identity"]

    @property
    def bundle_version(self) -> int:
        return self.hello["bundle_version"]

    def ping(self) -> dict:
        frames.write_frame(self.sock, {"type": "ping"})
        return frames.read_frame(self._reader)

    def whoami(self) -> dict:
        frames.write_frame(self.sock, {"type": "whoami"})
        return frames.read_frame(self._reader)

    def echo(self, payload) -> dict:
        frames.write_frame(self.sock, {"type": "echo", "payload": payload})
        return frames.read_frame(self._reader)

    def read_frame(self) -> dict:
        return frames.read_frame(self._reader)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def connect(*, host: str, port: int, cert_path: str | None,
            key_path: str | None, server_ca_pem: str,
            server_hostname: str = "localhost",
            timeout: float = 5.0) -> ClientSession:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cadata=server_ca_pem)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if cert_path is not None:
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    raw = socket.create_connection((host, port), timeout=timeout)
    tls = ctx.wrap_socket(raw, server_hostname=server_hostname)
    reader = tls.makefile("rb")
    hello = frames.read_frame(reader)
    if hello.get("type") == "error":
        tls.close()
        raise HandshakeRejected(hello)
    if hello.get("type") != "hello":
        tls.close()
        raise RuntimeError(f"unexpected first frame: {hello!r}")
    return ClientSession(tls, reader, hello)


class HandshakeRejected(Exception):
    """Server completed TLS but rejected the client at the app layer."""

    def __init__(self, error_frame: dict) -> None:
        super().__init__(error_frame.get("message", "rejected"))
        self.error_frame = error_frame
