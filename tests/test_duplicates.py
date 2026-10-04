"""Duplicates: a commitment is binding, a reveal counts exactly once."""

import pytest

from commit_reveal.errors import ErrorCode, ProtocolError

from tests.conftest import (
    ROUND_ID,
    commitment_for,
    commit_all,
    make_round,
    reveal,
)


def test_duplicate_commit_rejected(service, clock):
    make_round(service)
    service.commit("req-c1", ROUND_ID, "alice", commitment_for(ROUND_ID, "alice"))
    with pytest.raises(ProtocolError) as excinfo:
        service.commit("req-c2", ROUND_ID, "alice",
                       commitment_for(ROUND_ID, "alice"))
    assert excinfo.value.code == ErrorCode.DUPLICATE_COMMIT


def test_cannot_overwrite_commitment_with_different_digest(service, clock):
    make_round(service)
    service.commit("req-c1", ROUND_ID, "alice", commitment_for(ROUND_ID, "alice"))
    with pytest.raises(ProtocolError) as excinfo:
        service.commit("req-c2", ROUND_ID, "alice", "ab" * 32)
    assert excinfo.value.code == ErrorCode.DUPLICATE_COMMIT


def test_duplicate_reveal_rejected_and_counts_once(service, clock):
    make_round(service)
    commit_all(service, clock)
    reveal(service, ROUND_ID, "alice")
    with pytest.raises(ProtocolError) as excinfo:
        reveal(service, ROUND_ID, "alice")
    assert excinfo.value.code == ErrorCode.DUPLICATE_REVEAL
    assert service.get_round_view(ROUND_ID)["revealed"] == ["alice"]


def test_commit_by_unregistered_participant_rejected(service, clock):
    make_round(service)
    with pytest.raises(ProtocolError) as excinfo:
        service.commit("req-c1", ROUND_ID, "mallory", "ab" * 32)
    assert excinfo.value.code == ErrorCode.UNKNOWN_PARTICIPANT


def test_reveal_without_commitment_rejected(service, clock):
    from tests.conftest import COMMIT_DEADLINE, SECRETS

    make_round(service)
    service.commit("req-c1", ROUND_ID, "alice", commitment_for(ROUND_ID, "alice"))
    clock.advance_to(COMMIT_DEADLINE)
    s = SECRETS["bob"]
    with pytest.raises(ProtocolError) as excinfo:
        service.reveal("req-r1", ROUND_ID, "bob", s["random_value"], s["salt"])
    assert excinfo.value.code == ErrorCode.UNKNOWN_COMMITMENT
