"""Wire protocol: AAD encoding, nonce and key derivation.

This module is deliberately free of any AEAD library.  It defines the
byte-level contract that both the core crypto adapter (``cryptography``)
and the independent verifier (``PyCryptodome``) rely on, so a second
implementation can reproduce every value from the specification alone.

Layout
------
AAD (per chunk)::

    MAGIC      4 bytes   b"SAE1"   protocol/version marker
    msg_id    16 bytes   raw message identifier
    seq        8 bytes   big-endian chunk sequence number (0-based)
    flags      1 byte    bit0 = final-chunk marker
    pt_len     8 bytes   big-endian plaintext length of this chunk

Nonce (12 bytes, ChaCha20-Poly1305)::

    nonce_base 8 bytes  random, generated once per message, stored
    seq        4 bytes  big-endian, must be < 2**32

Message key::

    HKDF-SHA256(ikm=master_key, salt=per-message 16-byte salt,
                info=b"sae-msg-key-v1" || msg_id, L=32)

Nonce uniqueness argument: a nonce is (nonce_base, seq).  nonce_base is
random per message and the store enforces uniqueness of (message, seq),
so a (key, nonce) pair can never be issued twice for different content;
retries of the *same* content are answered from the stored record
instead of re-encrypting.
"""

from __future__ import annotations

import hashlib
import hmac
import struct

MAGIC = b"SAE1"
KEY_INFO_PREFIX = b"sae-msg-key-v1"

MSG_ID_LEN = 16
SALT_LEN = 16
NONCE_BASE_LEN = 8
NONCE_LEN = 12
KEY_LEN = 32
TAG_LEN = 16
MAX_SEQ = 2**32 - 1

FLAG_FINAL = 0x01

_AAD_STRUCT = struct.Struct(">4s16sQB Q".replace(" ", ""))  # MAGIC, msg_id, seq, flags, pt_len
AAD_LEN = _AAD_STRUCT.size


def encode_aad(msg_id: bytes, seq: int, final: bool, pt_len: int) -> bytes:
    """Encode the per-chunk associated data."""
    if len(msg_id) != MSG_ID_LEN:
        raise ValueError(f"msg_id must be {MSG_ID_LEN} bytes")
    if not 0 <= seq <= MAX_SEQ:
        raise ValueError("seq out of range")
    if pt_len < 0:
        raise ValueError("pt_len out of range")
    flags = FLAG_FINAL if final else 0
    return _AAD_STRUCT.pack(MAGIC, msg_id, seq, flags, pt_len)


def decode_aad(aad: bytes) -> dict:
    """Parse AAD back into its fields (used by verifiers and tests)."""
    if len(aad) != AAD_LEN:
        raise ValueError("bad AAD length")
    magic, msg_id, seq, flags, pt_len = _AAD_STRUCT.unpack(aad)
    if magic != MAGIC:
        raise ValueError("bad magic")
    return {
        "msg_id": msg_id,
        "seq": seq,
        "final": bool(flags & FLAG_FINAL),
        "pt_len": pt_len,
    }


def derive_nonce(nonce_base: bytes, seq: int) -> bytes:
    """Derive the per-chunk nonce from the per-message base and seq."""
    if len(nonce_base) != NONCE_BASE_LEN:
        raise ValueError(f"nonce_base must be {NONCE_BASE_LEN} bytes")
    if not 0 <= seq <= MAX_SEQ:
        raise ValueError("seq out of range")
    return nonce_base + seq.to_bytes(4, "big")


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    out = b""
    block = b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF-SHA256 (stdlib only, so any party can reproduce it)."""
    return _hkdf_expand(_hkdf_extract(salt, ikm), info, length)


def derive_message_key(master_key: bytes, salt: bytes, msg_id: bytes) -> bytes:
    """Derive the per-message AEAD key from the master key."""
    if len(salt) != SALT_LEN:
        raise ValueError(f"salt must be {SALT_LEN} bytes")
    if len(msg_id) != MSG_ID_LEN:
        raise ValueError(f"msg_id must be {MSG_ID_LEN} bytes")
    return hkdf_sha256(master_key, salt, KEY_INFO_PREFIX + msg_id, KEY_LEN)


def split_ciphertext(blob: bytes) -> tuple[bytes, bytes]:
    """Split a stored ``ciphertext || tag`` blob."""
    if len(blob) < TAG_LEN:
        raise ValueError("ciphertext shorter than tag")
    return blob[:-TAG_LEN], blob[-TAG_LEN:]
