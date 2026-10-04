"""Retry / nonce-reuse tests.

A nonce (message, seq) may never encrypt two different contents.
Identical retries are served from the stored record without
re-encryption; conflicting retries are rejected.
"""

from __future__ import annotations

import pytest

from sae.errors import NonceReuseConflict


def test_identical_retry_returns_stored_ciphertext(service):
    mid = service.create_message(request_id="req-c")["message_id"]
    first = service.submit_chunk(request_id="req-1", message_id=mid, seq=0,
                                 final=True, plaintext=b"payload")
    second = service.submit_chunk(request_id="req-2", message_id=mid, seq=0,
                                  final=True, plaintext=b"payload")
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["ciphertext"] == first["ciphertext"]
    # Stream still finalizes exactly once.
    service.finalize(request_id="req-f", message_id=mid)
    assert service.get_plaintext(request_id="req-g", message_id=mid) == b"payload"


def test_conflicting_retry_is_nonce_reuse_conflict(service):
    mid = service.create_message(request_id="req-c")["message_id"]
    service.submit_chunk(request_id="req-1", message_id=mid, seq=0,
                         final=True, plaintext=b"payload A")
    with pytest.raises(NonceReuseConflict) as excinfo:
        service.submit_chunk(request_id="req-2", message_id=mid, seq=0,
                             final=True, plaintext=b"payload B")
    assert excinfo.value.category == "nonce_reuse_conflict"
    # The original content must be the one that survives.
    service.finalize(request_id="req-f", message_id=mid)
    assert service.get_plaintext(request_id="req-g", message_id=mid) == b"payload A"


def test_same_content_with_different_final_flag_conflicts(service):
    mid = service.create_message(request_id="req-c")["message_id"]
    service.submit_chunk(request_id="req-1", message_id=mid, seq=0,
                         final=False, plaintext=b"payload")
    with pytest.raises(NonceReuseConflict):
        service.submit_chunk(request_id="req-2", message_id=mid, seq=0,
                             final=True, plaintext=b"payload")


def test_conflict_is_audited_with_request_ids(service):
    mid = service.create_message(request_id="req-c")["message_id"]
    service.submit_chunk(request_id="req-good", message_id=mid, seq=0,
                         final=True, plaintext=b"A")
    with pytest.raises(NonceReuseConflict):
        service.submit_chunk(request_id="req-bad", message_id=mid, seq=0,
                             final=True, plaintext=b"B")
    events = {e["event"]: e for e in service.audit.for_message(mid)}
    assert events["chunk_accepted"]["request_id"] == "req-good"
    rejected = events["chunk_rejected"]
    assert rejected["request_id"] == "req-bad"
    assert rejected["decision"] == "reject"
    # Audit must contain hashes, never plaintext.
    assert b"A".hex() not in rejected["detail_json"]
    assert "stored_pt_sha256" in rejected["detail_json"]
