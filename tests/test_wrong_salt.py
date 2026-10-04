"""Wrong-salt reveals are rejected with COMMITMENT_MISMATCH and not counted."""

import pytest

from commit_reveal.errors import ErrorCode, ProtocolError

from tests.conftest import ROUND_ID, SECRETS, commit_all, make_round, reveal


def test_wrong_salt_rejected_and_not_stored(service, clock):
    make_round(service)
    commit_all(service, clock)

    with pytest.raises(ProtocolError) as excinfo:
        reveal(service, ROUND_ID, "alice", salt="00" * 16)
    assert excinfo.value.code == ErrorCode.COMMITMENT_MISMATCH

    # The failed reveal must not be recorded...
    assert service.get_round_view(ROUND_ID)["revealed"] == []
    # ...and the correct reveal is still accepted afterwards.
    reveal(service, ROUND_ID, "alice")
    assert service.get_round_view(ROUND_ID)["revealed"] == ["alice"]


def test_wrong_random_value_rejected(service, clock):
    make_round(service)
    commit_all(service, clock)

    s = SECRETS["bob"]
    with pytest.raises(ProtocolError) as excinfo:
        service.reveal("req-reveal-bob", ROUND_ID, "bob", "00" * 32, s["salt"])
    assert excinfo.value.code == ErrorCode.COMMITMENT_MISMATCH


def test_mismatch_is_audited_with_masked_secrets(service, clock, audit):
    make_round(service)
    commit_all(service, clock)

    with pytest.raises(ProtocolError):
        reveal(service, ROUND_ID, "alice", salt="00" * 16)

    events = [e for e in audit.list_for_round(ROUND_ID)
              if e["event_type"] == "reveal" and e["outcome"] == "REJECTED"]
    assert len(events) == 1
    event = events[0]
    assert event["reason"] == ErrorCode.COMMITMENT_MISMATCH.value
    assert event["request_id"] == "req-reveal-alice"
    # Secrets must appear only as fingerprints, never in clear.
    detail = event["detail"]
    assert detail["random_value"].startswith("sha256:")
    assert detail["salt"].startswith("sha256:")
    assert SECRETS["alice"]["random_value"] not in str(detail)
    assert SECRETS["alice"]["salt"] not in str(detail)
