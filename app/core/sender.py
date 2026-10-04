"""Message sender: split, seal and frame a plaintext message.

This is the reference *producer* side of the protocol.  It is used by the
demo client and by tests, but the receiving :mod:`app.core.service` never
imports it: producer and consumer share only :mod:`app.core.protocol` and the
pluggable AEAD backends.
"""

from __future__ import annotations

from dataclasses import dataclass

from .crypto import AEADBackend
from .keyring import KeyBundle
from .protocol import derive_nonce, encode_aad, encode_frame


@dataclass(frozen=True)
class SealedMessage:
    message_id: str
    total_segments: int
    total_len: int
    key_id: str
    frames: list[bytes]


def seal_segment(bundle: KeyBundle, backend: AEADBackend, message_id: str,
                 seqno: int, total_segments: int, is_final: bool,
                 total_len: int, chunk: bytes) -> bytes:
    """Seal one chunk and return its wire frame.

    Deterministic for a given (bundle, slot, chunk): retrying the same slot
    reproduces byte-identical ciphertext, so a retry never reuses a nonce with
    different content.
    """
    nonce = derive_nonce(bundle.nonce_key, message_id, seqno, is_final)
    aad = encode_aad(message_id, seqno, total_segments, is_final, total_len,
                     len(chunk))
    ciphertext = backend.seal(bundle.aead_key, nonce, chunk, aad)
    return encode_frame(message_id, seqno, total_segments, total_len, is_final,
                        ciphertext)


def encode_message(bundle: KeyBundle, backend: AEADBackend, message_id: str,
                   plaintext: bytes, chunk_size: int) -> SealedMessage:
    """Split ``plaintext`` into fixed-ish chunks and seal every segment.

    The final chunk carries IS_FINAL; all other chunks are non-final.  An
    empty message produces exactly one, empty, final segment.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    total_len = len(plaintext)
    if total_len == 0:
        total_segments = 1
        chunks = [b""]
    else:
        chunks = [plaintext[i:i + chunk_size]
                  for i in range(0, total_len, chunk_size)]
        total_segments = len(chunks)

    frames: list[bytes] = []
    for seqno, chunk in enumerate(chunks):
        is_final = seqno == total_segments - 1
        frames.append(seal_segment(
            bundle, backend, message_id, seqno, total_segments, is_final,
            total_len, chunk))
    return SealedMessage(
        message_id=message_id,
        total_segments=total_segments,
        total_len=total_len,
        key_id=bundle.key_id,
        frames=frames,
    )
