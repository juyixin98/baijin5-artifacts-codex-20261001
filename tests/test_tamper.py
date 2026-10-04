"""Tamper and truncation tests.

Every scenario must (a) fail with the expected error category and
(b) leave no plaintext available - a failed stream must never output a
fake "complete" message.
"""

from __future__ import annotations

import pytest

from sae.errors import (
    FinalFlagMisplaced,
    IncompleteStream,
    MessageStateConflict,
    TagMismatch,
)

PLAINTEXT = b"sensitive payload " * 200  # 3400 bytes


def _stage(service, chunks, mid=None):
    mid = mid or service.create_message(request_id="req-create")["message_id"]
    for seq, (final, pt) in enumerate(chunks):
        service.submit_chunk(
            request_id=f"req-{seq}", message_id=mid, seq=seq, final=final, plaintext=pt
        )
    return mid


def _three_chunks():
    return [
        (False, PLAINTEXT[:1000]),
        (False, PLAINTEXT[1000:2000]),
        (True, PLAINTEXT[2000:]),
    ]


def _assert_no_plaintext(service, mid):
    with pytest.raises(MessageStateConflict) as excinfo:
        service.get_plaintext(request_id="req-get", message_id=mid)
    assert excinfo.value.category == "message_state_conflict"
    status = service.get_status(mid)
    assert status["status"] != "released"
    assert status["plaintext_sha256"] is None


def test_missing_middle_chunk_is_incomplete(service):
    chunks = _three_chunks()
    mid = service.create_message(request_id="req-c2")["message_id"]
    service.submit_chunk(request_id="r0", message_id=mid, seq=0, final=False,
                         plaintext=chunks[0][1])
    service.submit_chunk(request_id="r2", message_id=mid, seq=2, final=True,
                         plaintext=chunks[2][1])
    with pytest.raises(IncompleteStream) as excinfo:
        service.finalize(request_id="req-fin", message_id=mid)
    assert excinfo.value.category == "incomplete_stream"
    assert excinfo.value.context["missing"] == [1]
    _assert_no_plaintext(service, mid)


def test_missing_terminator_is_incomplete(service):
    chunks = _three_chunks()
    mid = _stage(service, [(False, pt) for _, pt in chunks])  # no final flag
    with pytest.raises(IncompleteStream) as excinfo:
        service.finalize(request_id="req-fin", message_id=mid)
    assert excinfo.value.category == "incomplete_stream"
    assert "terminating" in excinfo.value.reason
    _assert_no_plaintext(service, mid)


def test_truncated_tail_is_incomplete(service):
    chunks = _three_chunks()
    mid = _stage(service, chunks[:2])  # tail (with final flag) never arrives
    with pytest.raises(IncompleteStream):
        service.finalize(request_id="req-fin", message_id=mid)
    _assert_no_plaintext(service, mid)


def test_final_flag_on_non_last_chunk(service):
    chunks = _three_chunks()
    mid = _stage(service, [(True, chunks[0][1]), chunks[1], chunks[2]])
    with pytest.raises(FinalFlagMisplaced) as excinfo:
        service.finalize(request_id="req-fin", message_id=mid)
    assert excinfo.value.category == "final_flag_misplaced"
    _assert_no_plaintext(service, mid)


def test_two_final_flags_rejected(service):
    chunks = _three_chunks()
    mid = _stage(service, [chunks[0], (True, chunks[1][1]), chunks[2]])
    with pytest.raises(FinalFlagMisplaced):
        service.finalize(request_id="req-fin", message_id=mid)
    _assert_no_plaintext(service, mid)


def test_flipped_ciphertext_byte_is_tag_mismatch(service):
    mid = _stage(service, _three_chunks())
    chunk = service._store.get_chunk(mid, 1)
    ct = bytearray(chunk["ct"])
    ct[5] ^= 0x01
    service._store._conn.execute(
        "UPDATE chunks SET ct=? WHERE message_id=? AND seq=?",
        (bytes(ct), mid, 1),
    )
    service._store._conn.commit()
    with pytest.raises(TagMismatch) as excinfo:
        service.finalize(request_id="req-fin", message_id=mid)
    assert excinfo.value.category == "tag_mismatch"
    assert excinfo.value.context["seq"] == 1
    assert service.get_status(mid)["status"] == "failed"
    _assert_no_plaintext(service, mid)


def test_reordered_staged_chunks_are_detected(service):
    # Swap the ciphertexts of seq 0 and seq 1 in staging: each chunk is
    # bound to its seq via nonce+AAD, so authentication must fail.
    mid = _stage(service, _three_chunks())
    c0 = service._store.get_chunk(mid, 0)
    c1 = service._store.get_chunk(mid, 1)
    conn = service._store._conn
    conn.execute("UPDATE chunks SET ct=? WHERE message_id=? AND seq=0",
                 (c1["ct"], mid))
    conn.execute("UPDATE chunks SET ct=? WHERE message_id=? AND seq=1",
                 (c0["ct"], mid))
    conn.commit()
    with pytest.raises(TagMismatch):
        service.finalize(request_id="req-fin", message_id=mid)
    _assert_no_plaintext(service, mid)


def test_empty_stream_is_incomplete(service):
    mid = service.create_message(request_id="req-c")["message_id"]
    with pytest.raises(IncompleteStream):
        service.finalize(request_id="req-fin", message_id=mid)
    _assert_no_plaintext(service, mid)


def test_failed_message_cannot_be_resubmitted(service):
    mid = _stage(service, _three_chunks())
    chunk = service._store.get_chunk(mid, 0)
    ct = bytearray(chunk["ct"])
    ct[0] ^= 0xFF
    conn = service._store._conn
    conn.execute("UPDATE chunks SET ct=? WHERE message_id=? AND seq=0",
                 (bytes(ct), mid))
    conn.commit()
    with pytest.raises(TagMismatch):
        service.finalize(request_id="req-fin", message_id=mid)
    with pytest.raises(MessageStateConflict):
        service.submit_chunk(request_id="r", message_id=mid, seq=9,
                             final=True, plaintext=b"late")
