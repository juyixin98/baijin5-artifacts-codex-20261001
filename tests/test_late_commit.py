"""Late commitments are rejected once the commit set is frozen."""

import pytest

from commit_reveal.errors import ErrorCode, ProtocolError

from tests.conftest import (
    COMMIT_DEADLINE,
    ROUND_ID,
    commitment_for,
    make_round,
)


def test_commit_after_deadline_rejected(service, clock):
    make_round(service)
    service.commit("req-c1", ROUND_ID, "alice", commitment_for(ROUND_ID, "alice"))

    clock.advance_to(COMMIT_DEADLINE)  # deadline reached: set frozen
    with pytest.raises(ProtocolError) as excinfo:
        service.commit("req-c2", ROUND_ID, "bob", commitment_for(ROUND_ID, "bob"))
    assert excinfo.value.code == ErrorCode.LATE_COMMIT

    view = service.get_round_view(ROUND_ID)
    assert view["status"] == "FROZEN"
    assert view["committed"] == ["alice"]  # frozen set unchanged


def test_commit_at_last_moment_accepted(service, clock):
    make_round(service)
    clock.advance_to(COMMIT_DEADLINE - 1)
    service.commit("req-c1", ROUND_ID, "alice", commitment_for(ROUND_ID, "alice"))
    assert service.get_round_view(ROUND_ID)["committed"] == ["alice"]


def test_late_commit_is_audited(service, clock, audit):
    make_round(service)
    clock.advance_to(COMMIT_DEADLINE)
    with pytest.raises(ProtocolError):
        service.commit("req-late", ROUND_ID, "bob", commitment_for(ROUND_ID, "bob"))

    events = [e for e in audit.list_for_round(ROUND_ID)
              if e["event_type"] == "commit" and e["outcome"] == "REJECTED"]
    assert len(events) == 1
    assert events[0]["reason"] == ErrorCode.LATE_COMMIT.value
    assert events[0]["request_id"] == "req-late"
    assert events[0]["detail"]["round_status"] == "FROZEN"


def test_reveal_before_freeze_rejected(service, clock):
    from tests.conftest import SECRETS

    make_round(service)
    service.commit("req-c1", ROUND_ID, "alice", commitment_for(ROUND_ID, "alice"))
    s = SECRETS["alice"]
    with pytest.raises(ProtocolError) as excinfo:
        service.reveal("req-r1", ROUND_ID, "alice", s["random_value"], s["salt"])
    assert excinfo.value.code == ErrorCode.REVEAL_BEFORE_FREEZE
