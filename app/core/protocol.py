"""Protocol encoding for segmented AEAD, version 1.

Wire frame of one segment (all multi-byte integers big-endian)::

    magic            8 bytes  b"SSEAFRM1"
    version          uint8    1
    message_id_len   uint16
    message_id       bytes (UTF-8, 1..128 bytes)
    seqno            uint64   index of this segment, 0-based
    total_segments   uint64   declared total number of segments
    total_len        uint64   declared total plaintext length of the stream
    flags            uint8    bit0 = IS_FINAL
    ciphertext_len   uint32   length of AES-GCM ciphertext (incl. 16B tag)
    ciphertext       bytes

Every cleartext header field is also placed in the AAD, so the frame is
authenticate-the-headers: flipping any header byte changes the recomputed AAD
and fails the GCM tag.  Including ``total_len`` in the frame lets an
independent verifier reconstruct the exact AAD from wire bytes alone.

Security bindings
-----------------
* The 96-bit nonce is derived per slot via HMAC-SHA256 over
  ``message_id || seqno || is_final``.  A final and a non-final segment at the
  same index get different nonces, and frames moved between streams or
  positions cannot be replayed under a valid nonce.
* The AAD binds protocol version, message identity, sequence number, total
  segment count, final flag, *total message length* and *this segment's
  plaintext length*.  Deletion, reordering, truncation and length lying are
  all detectable on top of the GCM tag.

The same (message_id, seqno, is_final) therefore always maps to the same
nonce.  A retry that re-seals the identical plaintext reproduces identical
ciphertext; sealing *different* content under that nonce is rejected by the
service's nonce-conflict check.
"""

from __future__ import annotations

import hmac
import hashlib
import struct
from dataclasses import dataclass

from .errors import ProtocolCodingError

PROTOCOL_VERSION = 1
MAGIC = b"SSEAFRM1"
NONCE_DOMAIN = b"SSEA1-NONCE"
AAD_DOMAIN = b"SSEA1-AAD"

NONCE_LEN = 12
TAG_LEN = 16
FLAG_FINAL = 0x01

MESSAGE_ID_MAX = 128
MAX_UINT32 = 0xFFFFFFFF
MAX_UINT64 = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class SegmentFrame:
    """One parsed, not-yet-authenticated segment frame."""

    version: int
    message_id: bytes
    seqno: int
    total_segments: int
    total_len: int
    is_final: bool
    ciphertext: bytes

    @property
    def plaintext_len(self) -> int:
        return len(self.ciphertext) - TAG_LEN


def _u8(value: int, name: str) -> bytes:
    if not 0 <= value <= 0xFF:
        raise ProtocolCodingError(f"{name} out of uint8 range", value=value)
    return struct.pack(">B", value)


def _u16(value: int, name: str) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ProtocolCodingError(f"{name} out of uint16 range", value=value)
    return struct.pack(">H", value)


def _u32(value: int, name: str) -> bytes:
    if not 0 <= value <= MAX_UINT32:
        raise ProtocolCodingError(f"{name} out of uint32 range", value=value)
    return struct.pack(">I", value)


def _u64(value: int, name: str) -> bytes:
    if not 0 <= value <= MAX_UINT64:
        raise ProtocolCodingError(f"{name} out of uint64 range", value=value)
    return struct.pack(">Q", value)


def message_id_bytes(message_id: str | bytes) -> bytes:
    """Normalise and validate a message id."""
    mid = message_id.encode("utf-8") if isinstance(message_id, str) else message_id
    if not 1 <= len(mid) <= MESSAGE_ID_MAX:
        raise ProtocolCodingError(
            "message_id must be 1..%d UTF-8 bytes" % MESSAGE_ID_MAX,
            length=len(mid))
    return mid


def derive_nonce(
    nonce_key: bytes,
    message_id: str | bytes,
    seqno: int,
    is_final: bool,
) -> bytes:
    """Derive the unique 96-bit nonce for a segment slot.

    Deterministic and bound to message identity, sequence number and the
    termination marker.  ``nonce_key`` is independent of the AEAD key.
    """
    if len(nonce_key) < 32:
        raise ProtocolCodingError("nonce derivation key must be >= 32 bytes",
                                  key_len=len(nonce_key))
    mid = message_id_bytes(message_id)
    msg = b"".join((
        NONCE_DOMAIN,
        _u8(PROTOCOL_VERSION, "version"),
        _u16(len(mid), "message_id_len"),
        mid,
        _u64(seqno, "seqno"),
        _u8(FLAG_FINAL if is_final else 0, "flags"),
    ))
    return hmac.new(nonce_key, msg, hashlib.sha256).digest()[:NONCE_LEN]


def encode_aad(
    message_id: str | bytes,
    seqno: int,
    total_segments: int,
    is_final: bool,
    total_len: int,
    segment_plaintext_len: int,
) -> bytes:
    """Build the canonical associated data authenticated alongside a segment."""
    mid = message_id_bytes(message_id)
    return b"".join((
        AAD_DOMAIN,
        _u8(PROTOCOL_VERSION, "version"),
        _u16(len(mid), "message_id_len"),
        mid,
        _u64(seqno, "seqno"),
        _u64(total_segments, "total_segments"),
        _u64(total_len, "total_len"),
        _u8(FLAG_FINAL if is_final else 0, "flags"),
        _u64(segment_plaintext_len, "segment_plaintext_len"),
    ))


def encode_frame(
    message_id: str | bytes,
    seqno: int,
    total_segments: int,
    total_len: int,
    is_final: bool,
    ciphertext: bytes,
) -> bytes:
    """Serialise a segment to the canonical v1 wire frame."""
    mid = message_id_bytes(message_id)
    _u64(seqno, "seqno")
    _u64(total_segments, "total_segments")
    _u64(total_len, "total_len")
    _u32(len(ciphertext), "ciphertext_len")
    return b"".join((
        MAGIC,
        _u8(PROTOCOL_VERSION, "version"),
        _u16(len(mid), "message_id_len"),
        mid,
        _u64(seqno, "seqno"),
        _u64(total_segments, "total_segments"),
        _u64(total_len, "total_len"),
        _u8(FLAG_FINAL if is_final else 0, "flags"),
        _u32(len(ciphertext), "ciphertext_len"),
        ciphertext,
    ))


def decode_frame(buf: bytes) -> SegmentFrame:
    """Parse a v1 frame, rejecting malformed framing with PROTOCODING."""
    if not isinstance(buf, (bytes, bytearray)):
        raise ProtocolCodingError("frame must be bytes")
    if len(buf) < len(MAGIC) + 1:
        raise ProtocolCodingError("frame shorter than magic+version",
                                  got=len(buf))
    if bytes(buf[: len(MAGIC)]) != MAGIC:
        raise ProtocolCodingError("bad magic")
    pos = len(MAGIC)

    def take(n: int, what: str) -> bytes:
        nonlocal pos
        if pos + n > len(buf):
            raise ProtocolCodingError(f"truncated frame reading {what}",
                                      offset=pos, need=n,
                                      have=len(buf) - pos)
        chunk = bytes(buf[pos:pos + n])
        pos += n
        return chunk

    (version,) = struct.unpack(">B", take(1, "version"))
    if version != PROTOCOL_VERSION:
        raise ProtocolCodingError("unsupported protocol version",
                                  version=version)
    (mid_len,) = struct.unpack(">H", take(2, "message_id_len"))
    if not 1 <= mid_len <= MESSAGE_ID_MAX:
        raise ProtocolCodingError("message_id_len out of range",
                                  length=mid_len)
    mid = take(mid_len, "message_id")
    (seqno,) = struct.unpack(">Q", take(8, "seqno"))
    (total_segments,) = struct.unpack(">Q", take(8, "total_segments"))
    (total_len,) = struct.unpack(">Q", take(8, "total_len"))
    (flags,) = struct.unpack(">B", take(1, "flags"))
    if flags & ~FLAG_FINAL:
        raise ProtocolCodingError("unknown flag bits set", flags=flags)
    (ct_len,) = struct.unpack(">I", take(4, "ciphertext_len"))
    if ct_len < TAG_LEN:
        raise ProtocolCodingError("ciphertext shorter than GCM tag",
                                  ciphertext_len=ct_len)
    ct = take(ct_len, "ciphertext")
    if pos != len(buf):
        raise ProtocolCodingError("trailing bytes after frame",
                                  trailing=len(buf) - pos)
    if total_segments == 0:
        raise ProtocolCodingError("total_segments must be >= 1")
    if seqno >= total_segments:
        raise ProtocolCodingError("seqno >= total_segments",
                                  seqno=seqno, total_segments=total_segments)
    return SegmentFrame(
        version=version,
        message_id=mid,
        seqno=seqno,
        total_segments=total_segments,
        total_len=total_len,
        is_final=bool(flags & FLAG_FINAL),
        ciphertext=ct,
    )
