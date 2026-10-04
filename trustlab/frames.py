"""Length-prefixed JSON frame protocol used on the mTLS data plane.

Wire format: a 4-byte big-endian unsigned length followed by a UTF-8 JSON
object. This module is the single place that knows the encoding; both the
server (tls_plane) and the client helper (clientlib) go through it.

Error contract:
- clean EOF or partial frame           -> ConnectionClosed
- declared length above the limit      -> ResourceExhausted
- undecodable / structurally bad JSON  -> InputError
"""
from __future__ import annotations

import json
import struct
from typing import BinaryIO

from .errors import InputError, ResourceExhausted

DEFAULT_MAX_FRAME_BYTES = 64 * 1024
_LEN = struct.Struct(">I")


class ConnectionClosed(Exception):
    """Peer closed the connection (clean EOF or partial frame)."""


def encode_frame(message: dict) -> bytes:
    payload = json.dumps(
        message, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return _LEN.pack(len(payload)) + payload


def read_frame(reader: BinaryIO, *,
               max_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> dict:
    header = _read_exact(reader, _LEN.size)
    if header is None:
        raise ConnectionClosed()
    (length,) = _LEN.unpack(header)
    if length > max_bytes:
        raise ResourceExhausted(
            f"frame length {length} exceeds limit {max_bytes}",
            detail={"length": length, "max_bytes": max_bytes},
            reasoning=(
                "declared frame size exceeded the configured per-frame "
                "limit; connection dropped to bound memory use"
            ),
        )
    payload = _read_exact(reader, length)
    if payload is None:
        raise ConnectionClosed()
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"malformed frame payload: {exc}") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise InputError("frame must be a JSON object with a string 'type' field")
    return message


def write_frame(writer, message: dict) -> None:
    data = encode_frame(message)
    writer.write(data)
    flush = getattr(writer, "flush", None)
    if callable(flush):
        flush()


def _read_exact(reader: BinaryIO, n: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < n:
        chunk = reader.read(n - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)
