"""Independent offline verifier (separate responsibility from the service).

Given a complete set of wire frames and a key bundle, this module decides
whether the stream is authentic and complete *without* the service state
machine, database or staging area.  It exists so that:

* acceptance tests have an oracle independent of the code under test, and
* operators can re-verify a captured segment set with a different AEAD
  library than the one that sealed it.

The verifier returns an explicit verdict: ``ACCEPT`` or ``REJECT`` with a
machine-readable reason.  It never returns plaintext on a rejecting path and
treats undecidable input (gaps, missing terminator) as ``REJECT``.

All AAD material is reconstructed purely from the wire frames
(``total_len`` is a header field), so no value produced by the receiving
service is trusted here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .crypto import AEADBackend
from .errors import AuthenticationError
from .keyring import KeyBundle
from .protocol import SegmentFrame, decode_frame, derive_nonce, encode_aad


class Verdict(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True)
class VerificationResult:
    verdict: Verdict
    reason: str
    detail: dict[str, object] = field(default_factory=dict)
    plaintext: bytes | None = None


def _reject(reason: str, **detail: object) -> VerificationResult:
    return VerificationResult(Verdict.REJECT, reason, detail)


def verify_stream(bundle: KeyBundle, backend: AEADBackend,
                  frames: list[bytes]) -> VerificationResult:
    """Independently verify structure, ordering, termination and every tag."""
    if not frames:
        return _reject("empty_stream")

    parsed: list[SegmentFrame] = []
    message_id: bytes | None = None
    total_segments: int | None = None
    total_len: int | None = None

    for i, raw in enumerate(frames):
        try:
            frame = decode_frame(raw)
        except Exception as exc:  # noqa: BLE001 - any coding flaw rejects
            return _reject("protocoding", index=i, error=str(exc))

        if message_id is None:
            message_id = frame.message_id
        elif frame.message_id != message_id:
            return _reject("message_id_mismatch", index=i)

        if total_segments is None:
            total_segments = frame.total_segments
            total_len = frame.total_len
        elif (frame.total_segments != total_segments
              or frame.total_len != total_len):
            return _reject("header_changed", index=i)

        if frame.seqno != i:
            return _reject("reorder_or_gap", index=i, seqno=frame.seqno)

        expect_final = i == len(frames) - 1
        if frame.is_final != expect_final:
            return _reject(
                "missing_terminator" if expect_final else "early_terminator",
                index=i)
        parsed.append(frame)

    assert total_segments is not None and total_len is not None
    if total_segments != len(frames):
        return _reject("truncation", declared=total_segments,
                       present=len(frames))

    pieces: list[bytes] = []
    for frame in parsed:
        nonce = derive_nonce(bundle.nonce_key, frame.message_id, frame.seqno,
                             frame.is_final)
        aad = encode_aad(frame.message_id, frame.seqno, frame.total_segments,
                         frame.is_final, frame.total_len, frame.plaintext_len)
        try:
            piece = backend.open(bundle.aead_key, nonce, frame.ciphertext, aad)
        except AuthenticationError:
            return _reject("auth_failed", seqno=frame.seqno)
        if len(piece) != frame.plaintext_len:
            return _reject("length_inconsistent", seqno=frame.seqno)
        pieces.append(piece)

    assembled = b"".join(pieces)
    if len(assembled) != total_len:
        return _reject("truncation", declared=total_len,
                       assembled=len(assembled))
    return VerificationResult(
        Verdict.ACCEPT, "ok",
        {"segments": len(frames), "total_len": total_len},
        plaintext=assembled)
