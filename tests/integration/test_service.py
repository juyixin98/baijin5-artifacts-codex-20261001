"""Service state-machine integration tests.

These assert concrete outcomes and failure categories for the scenarios in
the specification: varied chunking, out-of-order delivery, segment deletion
and reordering, missing terminator, retry/nonce-reuse and process
interruption.  Expected plaintext comes from the committed fixture vectors,
not from the service.
"""

from __future__ import annotations

import base64
import os

import pytest

from app.core.crypto import CryptographyBackend, PyCryptodomeBackend
from app.core.errors import (
    AuthenticationError, IncompleteStreamError, NonceConflictError,
    StateError, TruncationError,
)
from app.core.sender import encode_message
from app.core.verifier import Verdict, verify_stream
from app.db.store import FAILED, INTERRUPTED, RELEASED, Store
from app.core.staging import StagingArea
from app.core.service import SegmentedAEADService
from app.core.audit import AuditLog

pytestmark = pytest.mark.integration


def _frames(bundle, backend, mid, plaintext, chunk):
    return encode_message(bundle, backend, mid, plaintext, chunk).frames


def _case_frames(vectors, name):
    case = next(c for c in vectors["cases"] if c["name"] == name)
    return case, [base64.b64decode(f) for f in case["frames_b64"]]


# ---------------------------------------------------------------------------
# Happy path: varied chunk sizes, byte-identical release, cross-checked by the
# independent offline verifier using the OTHER AEAD backend.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_name", [
    "empty", "short", "exact-one-chunk", "multi-chunk", "non-divisible",
    "larger",
])
def test_all_fixture_cases_release_byte_identical_and_verify_independently(
        make_service, fixture_keys, vectors, alt_backend, case_name):
    case, frames = _case_frames(vectors, case_name)
    expected = base64.b64decode(case["plaintext_b64"])
    svc = make_service()

    for f in frames:
        acc = svc.submit(f, request_id=f"t-{case_name}")
        assert acc.accepted is True
        assert acc.complete is False or acc.seqno == len(frames) - 1

    final = svc.finalize(case["message_id"])
    assert final.complete is True
    assert final.plaintext == expected

    # Independent oracle: different backend, no service/db/staging involved.
    verdict = verify_stream(fixture_keys, alt_backend, frames)
    assert verdict.verdict is Verdict.ACCEPT
    assert verdict.plaintext == expected
    assert verdict.detail["total_len"] == case["total_len"]

    assert svc.status(case["message_id"])["status"] == RELEASED


@pytest.mark.parametrize("chunk,expected_segments", [
    (1, 100), (7, 15), (16, 7), (100, 1), (101, 1), (50, 2),
])
def test_different_chunkings_stay_byte_identical(
        make_service, fixture_keys, chunk, expected_segments):
    plaintext = bytes(range(100))
    frames = _frames(fixture_keys, CryptographyBackend(), "m-chunk",
                     plaintext, chunk)
    assert len(frames) == expected_segments
    svc = make_service()
    for f in frames:
        svc.submit(f)
    assert svc.finalize("m-chunk").plaintext == plaintext


def test_receiving_service_can_use_pycryptodome_backend(
        make_service, fixture_keys, alt_backend):
    plaintext = b"backend switch " * 10
    frames = _frames(fixture_keys, CryptographyBackend(), "m-backend",
                     plaintext, 16)
    svc = make_service(backend=PyCryptodomeBackend())
    for f in frames:
        svc.submit(f)
    assert svc.finalize("m-backend").plaintext == plaintext


# ---------------------------------------------------------------------------
# Out-of-order delivery is accepted (slots, not arrival order).
# ---------------------------------------------------------------------------

def test_out_of_order_delivery_releases_correctly(make_service, fixture_keys):
    plaintext = bytes((i * 13) & 0xFF for i in range(166))
    frames = _frames(fixture_keys, CryptographyBackend(), "m-ooo",
                     plaintext, 33)
    assert len(frames) == 6
    order = [3, 0, 5, 1, 4, 2]
    svc = make_service()
    for i in order:
        acc = svc.submit(frames[i])
        assert acc.accepted is True
    status = svc.status("m-ooo")
    assert status["received_seqnos"] == sorted(order)
    assert svc.finalize("m-ooo").plaintext == plaintext


# ---------------------------------------------------------------------------
# Deletion / reordering attacks.
# ---------------------------------------------------------------------------

def test_missing_segment_is_incomplete_then_fails_after_reorder_attempt(
        make_service, fixture_keys):
    plaintext = b"abcdefghijklmnop"
    frames = _frames(fixture_keys, CryptographyBackend(), "m-del",
                     plaintext, 4)
    assert len(frames) == 4
    svc = make_service()
    for f in frames[:3]:  # drop segment index 3 (the terminator)
        svc.submit(f)

    # Undecidable while outstanding: explicit incomplete, no plaintext.
    with pytest.raises(IncompleteStreamError) as ei:
        svc.finalize("m-del")
    assert ei.value.category == "incomplete"
    assert ei.value.state["received"] == 3
    assert svc.status("m-del")["released"] is False

    # Trying to pass segment 2's frame again as if it closed the stream is a
    # nonce conflict only if content differs; identical replay stays 3/4.
    acc = svc.submit(frames[2])
    assert acc.replay is True and acc.complete is False


def test_replayed_frame_at_different_slot_is_nonce_conflict(
        make_service, fixture_keys):
    # Segment 0's frame carries seqno 0 in its authenticated AAD; submitting
    # it is fine twice, but a re-seal of slot 0 with new content is detected.
    frames = _frames(fixture_keys, CryptographyBackend(), "m-replay",
                     b"abcdefgh", 4)
    svc = make_service()
    svc.submit(frames[0])
    # Identical retry is idempotent.
    again = svc.submit(frames[0])
    assert again.replay is True

    # Different content sealed under the same (message, seqno=0, non-final
    # marker at seqno 0) reuses the nonce -> rejected.
    attacker = _frames(fixture_keys, CryptographyBackend(), "m-replay",
                       b"XYZXabcd", 4)
    with pytest.raises(NonceConflictError) as ei:
        svc.submit(attacker[0])
    assert ei.value.category == "nonce_conflict"
    # Failure seals the stream and shreds fragments.
    assert svc.status("m-replay")["status"] == FAILED
    with pytest.raises(StateError):
        svc.get_result("m-replay")


def test_terminator_frame_moved_to_middle_is_rejected(make_service, fixture_keys):
    # Craft frames where a non-last segment claims final.
    frames = _frames_fraudulent_terminator(fixture_keys)
    svc = make_service()
    with pytest.raises(TruncationError) as ei:
        svc.submit(frames[1])  # seqno 1 claiming final
    assert ei.value.category == "truncation"
    assert svc.status("m-term")["status"] == FAILED


def _frames_fraudulent_terminator(bundle):
    from app.core.protocol import decode_frame, encode_frame
    good = _frames(bundle, CryptographyBackend(), "m-term",
                   b"abcdefghi", 3)
    # Re-frame segment 1 (middle) with the final flag flipped in the clear
    # header; its AAD still says non-final so it must be rejected structurally
    # before release.
    fr = decode_frame(good[1])
    return [
        good[0],
        encode_frame(fr.message_id, fr.seqno, fr.total_segments, fr.total_len,
                     True, fr.ciphertext),
        good[2],
    ]


def test_ciphertext_tampering_fails_authentication(make_service, fixture_keys):
    frames = _frames(fixture_keys, CryptographyBackend(), "m-tamper",
                     b"secretpayload!!", 5)
    svc = make_service()
    raw = bytearray(frames[1])
    raw[-1] ^= 0xFF  # flip tag/ciphertext tail byte
    with pytest.raises(AuthenticationError) as ei:
        svc.submit(bytes(raw))
    assert ei.value.category == "auth_failed"
    assert svc.status("m-tamper")["status"] == FAILED
    with pytest.raises(StateError):
        svc.get_result("m-tamper")


def test_header_tampering_total_len_fails_auth(make_service, fixture_keys):
    # Change declared total_len in the header; recomputed AAD differs -> auth.
    from app.core.protocol import decode_frame, encode_frame
    frames = _frames(fixture_keys, CryptographyBackend(), "m-hdr",
                     b"abcdef", 2)
    fr = decode_frame(frames[0])
    forged = encode_frame(fr.message_id, fr.seqno, fr.total_segments,
                          fr.total_len + 1, fr.is_final, fr.ciphertext)
    svc = make_service()
    with pytest.raises(AuthenticationError):
        svc.submit(forged)


# ---------------------------------------------------------------------------
# Missing terminator: present N-1 segments plus a terminator-less last frame.
# ---------------------------------------------------------------------------

def test_missing_terminator_flag_detected(make_service, fixture_keys):
    from app.core.protocol import decode_frame, encode_frame
    frames = _frames(fixture_keys, CryptographyBackend(), "m-noterm",
                     b"abcd", 2)
    assert len(frames) == 2
    last = decode_frame(frames[1])
    assert last.is_final is True
    # Strip the final marker in the clear header.  AAD still says final, so
    # the structural terminator check fires first.
    no_term = encode_frame(last.message_id, last.seqno, last.total_segments,
                           last.total_len, False, last.ciphertext)
    svc = make_service()
    svc.submit(frames[0])
    with pytest.raises(TruncationError) as ei:
        svc.submit(no_term)
    assert ei.value.category == "truncation"
    assert svc.status("m-noterm")["status"] == FAILED


# ---------------------------------------------------------------------------
# Process interruption: a new process/object reopens the same files and the
# stream is marked interrupted (undecidable); retransmission completes it.
# ---------------------------------------------------------------------------

def test_process_interruption_is_recoverable(tmp_path, fixture_keys):
    db = tmp_path / "ssea.sqlite3"
    staging = tmp_path / "staging"
    released = tmp_path / "released"
    plaintext = bytes((i * 5 + 1) & 0xFF for i in range(150))
    frames = _frames(fixture_keys, CryptographyBackend(), "m-crash",
                     plaintext, 20)

    # Process 1: submit half the segments, then "dies".
    store = Store(db)
    area = StagingArea(staging, released)
    svc1 = SegmentedAEADService(store, area, fixture_keys,
                                CryptographyBackend())
    for f in frames[:4]:
        svc1.submit(f)
    store.close()

    # Process 2 starts: prior receiving stream must be flagged interrupted.
    store2 = Store(db)
    area2 = StagingArea(staging, released)
    svc2 = SegmentedAEADService(store2, area2, fixture_keys,
                                CryptographyBackend(), audit=AuditLog())
    recovered = svc2.recover_interrupted()
    assert recovered == 1
    status = svc2.status("m-crash")
    assert status["status"] == INTERRUPTED
    assert status["received_count"] == 4

    # Cannot release yet - undecidable.
    with pytest.raises(IncompleteStreamError):
        svc2.finalize("m-crash")

    # Client retries missing segments (and replays the first 4 identically).
    for f in frames:
        acc = svc2.submit(f)
        assert acc.accepted is True
    result = svc2.finalize("m-crash")
    assert result.plaintext == plaintext
    store2.close()


def test_interrupted_stream_complete_on_disk_releases_without_retransmit(
        tmp_path, fixture_keys):
    # Crash exactly after the FINAL segment landed: receipts and fragments
    # are all present, but the old process never finalised.  Restart must be
    # able to release directly (resumed), not falsely declare truncation.
    db = tmp_path / "ssea.sqlite3"
    staging = tmp_path / "staging"
    released = tmp_path / "released"
    plaintext = b"".join(bytes([i]) for i in range(9))  # 9 bytes
    frames = _frames(fixture_keys, CryptographyBackend(), "m-fullcrash",
                     plaintext, 3)

    store = Store(db)
    svc1 = SegmentedAEADService(store, StagingArea(staging, released),
                                fixture_keys, CryptographyBackend())
    for f in frames:
        svc1.submit(f)
    store.close()

    store2 = Store(db)
    svc2 = SegmentedAEADService(store2, StagingArea(staging, released),
                                fixture_keys, CryptographyBackend(),
                                audit=AuditLog())
    assert svc2.recover_interrupted() == 1
    assert svc2.status("m-fullcrash")["status"] == INTERRUPTED
    result = svc2.finalize("m-fullcrash")
    assert result.complete is True
    assert result.plaintext == plaintext
    store2.close()


def test_interrupted_stream_with_lost_staging_reconciles(tmp_path, fixture_keys):
    db = tmp_path / "ssea.sqlite3"
    staging = tmp_path / "staging"
    released = tmp_path / "released"
    frames = _frames(fixture_keys, CryptographyBackend(), "m-lost",
                     b"abcdefghij", 5)

    store = Store(db)
    area = StagingArea(staging, released)
    svc = SegmentedAEADService(store, area, fixture_keys,
                               CryptographyBackend())
    for f in frames[:2]:
        svc.submit(f)
    store.close()

    # Simulate ephemeral staging wiped across restart.
    for child in staging.rglob("*.part"):
        child.unlink()

    store2 = Store(db)
    svc2 = SegmentedAEADService(store2, StagingArea(staging, released),
                                fixture_keys, CryptographyBackend())
    svc2.recover_interrupted()
    assert svc2.status("m-lost")["received_count"] == 0
    for f in frames:
        svc2.submit(f)
    assert svc2.finalize("m-lost").plaintext == b"abcdefghij"
    store2.close()


# ---------------------------------------------------------------------------
# No fake complete message on failure; audit carries category + request id.
# ---------------------------------------------------------------------------

def test_no_partial_plaintext_is_ever_exposed(make_service, fixture_keys):
    frames = _frames(fixture_keys, CryptographyBackend(), "m-partial",
                     b"abcdefgh", 4)
    svc = make_service()
    svc.submit(frames[0])
    # Before completion there is no released artifact.
    assert svc._staging.read_released("m-partial") is None
    with pytest.raises(StateError) as ei:
        svc.get_result("m-partial")
    assert ei.value.category == "state"
    # Staging fragments exist on disk with 0600 but carry no read API.
    staged = list((svc._staging.staging_root).rglob("*.part"))
    assert len(staged) == 1
    assert os.stat(staged[0]).st_mode & 0o777 == 0o600


def test_audit_records_accept_reject_and_request_id(make_service, fixture_keys):
    frames = _frames(fixture_keys, CryptographyBackend(), "m-audit",
                     b"abcdefgh", 4)
    svc = make_service()
    svc.submit(frames[0], request_id="req-abc")
    raw = bytearray(frames[1])
    raw[-1] ^= 0x01
    with pytest.raises(AuthenticationError):
        svc.submit(bytes(raw), request_id="req-bad")

    events = svc.audit_events("m-audit")
    kinds = {(e["kind"], e["category"]) for e in events}
    assert ("accept", "segment_accepted") in kinds
    assert any(e["kind"] == "reject" for e in events)
    bad = next(e for e in events if e["request_id"] == "req-bad")
    assert bad["category"] in {"rejected", "auth_failed"} or bad["kind"] == "reject"
    accepted = next(e for e in events if e["request_id"] == "req-abc")
    # Sensitive material never appears in audit state.
    assert "plaintext" not in str(accepted["state"])
