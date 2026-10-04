"""Tests for the independent offline verifier.

The verifier is an alternative decision implementation; here it is challenged
with structural and cryptographic corruptions and must return REJECT with a
specific reason, while accepting untouched fixture vectors.
"""

from __future__ import annotations

import base64

import pytest

from app.core.crypto import CryptographyBackend, PyCryptodomeBackend
from app.core.protocol import decode_frame, encode_frame
from app.core.verifier import Verdict, verify_stream

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("case_name", [
    "empty", "short", "exact-one-chunk", "multi-chunk", "non-divisible",
    "larger",
])
def test_accepts_fixture_vectors_with_each_backend(
        fixture_keys, vectors, case_name):
    case = next(c for c in vectors["cases"] if c["name"] == case_name)
    frames = [base64.b64decode(f) for f in case["frames_b64"]]
    expected = base64.b64decode(case["plaintext_b64"])

    v1 = verify_stream(fixture_keys, CryptographyBackend(), frames)
    v2 = verify_stream(fixture_keys, PyCryptodomeBackend(), frames)
    assert v1.verdict is Verdict.ACCEPT and v2.verdict is Verdict.ACCEPT
    assert v1.plaintext == v2.plaintext == expected


def test_rejects_empty_input(fixture_keys):
    r = verify_stream(fixture_keys, CryptographyBackend(), [])
    assert r.verdict is Verdict.REJECT and r.reason == "empty_stream"
    assert r.plaintext is None


def test_rejects_reordering(fixture_keys, vectors):
    case = next(c for c in vectors["cases"] if c["name"] == "multi-chunk")
    frames = [base64.b64decode(f) for f in case["frames_b64"]]
    frames[0], frames[1] = frames[1], frames[0]
    r = verify_stream(fixture_keys, CryptographyBackend(), frames)
    assert r.verdict is Verdict.REJECT
    assert r.reason == "reorder_or_gap"


def test_rejects_deleted_segment(fixture_keys, vectors):
    case = next(c for c in vectors["cases"] if c["name"] == "multi-chunk")
    frames = [base64.b64decode(f) for f in case["frames_b64"]]
    r = verify_stream(fixture_keys, CryptographyBackend(), frames[:-1])
    assert r.verdict is Verdict.REJECT
    # Either the terminator is missing or declared count mismatches.
    assert r.reason in {"missing_terminator", "truncation"}


def test_rejects_early_terminator(fixture_keys, vectors):
    case = next(c for c in vectors["cases"] if c["name"] == "multi-chunk")
    frames = [base64.b64decode(f) for f in case["frames_b64"]]
    fr = decode_frame(frames[0])
    frames[0] = encode_frame(fr.message_id, fr.seqno, fr.total_segments,
                             fr.total_len, True, fr.ciphertext)
    r = verify_stream(fixture_keys, CryptographyBackend(), frames)
    assert r.verdict is Verdict.REJECT and r.reason == "early_terminator"


def test_rejects_mixed_message_ids(fixture_keys, vectors):
    case = next(c for c in vectors["cases"] if c["name"] == "multi-chunk")
    frames = [base64.b64decode(f) for f in case["frames_b64"]]
    fr = decode_frame(frames[1])
    frames[1] = encode_frame(b"other-id", fr.seqno, fr.total_segments,
                             fr.total_len, fr.is_final, fr.ciphertext)
    r = verify_stream(fixture_keys, CryptographyBackend(), frames)
    assert r.verdict is Verdict.REJECT
    assert r.reason in {"message_id_mismatch", "protocoding", "auth_failed"}


def test_rejects_tampered_ciphertext(fixture_keys, vectors):
    case = next(c for c in vectors["cases"] if c["name"] == "multi-chunk")
    frames = [bytearray(base64.b64decode(f)) for f in case["frames_b64"]]
    frames[2][-1] ^= 0x01
    r = verify_stream(fixture_keys, CryptographyBackend(),
                      [bytes(f) for f in frames])
    assert r.verdict is Verdict.REJECT and r.reason == "auth_failed"
    assert r.plaintext is None


def test_rejects_garbage_frame(fixture_keys):
    r = verify_stream(fixture_keys, CryptographyBackend(), [b"not a frame"])
    assert r.verdict is Verdict.REJECT and r.reason == "protocoding"


def test_rejects_wrong_total_len_header(fixture_keys, vectors):
    case = next(c for c in vectors["cases"] if c["name"] == "multi-chunk")
    frames = [base64.b64decode(f) for f in case["frames_b64"]]
    fr = decode_frame(frames[0])
    frames[0] = encode_frame(fr.message_id, fr.seqno, fr.total_segments,
                             fr.total_len + 9, fr.is_final, fr.ciphertext)
    r = verify_stream(fixture_keys, CryptographyBackend(), frames)
    # Header disagree across frames (later frames carry original total).
    assert r.verdict is Verdict.REJECT
    assert r.reason in {"header_changed", "auth_failed"}
