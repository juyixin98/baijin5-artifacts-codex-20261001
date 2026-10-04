"""Selective abstain: unrevealed commitments are excluded from seed and draw.

This test class documents the protocol's central limitation: a participant
who commits but never reveals is excluded from the outcome. That keeps the
round finalizable, but it also means a last-mover who dislikes the impending
result can abort and change the candidate set — the bias risk spelled out in
the README.
"""

from commit_reveal.verify.verifier import verify_evidence

from tests.conftest import (
    REVEAL_DEADLINE,
    ROUND_ID,
    commit_all,
    make_round,
    reveal,
)


def test_abstainer_excluded_from_seed_and_draw(service, clock):
    make_round(service)
    commit_all(service, clock)
    reveal(service, ROUND_ID, "alice")
    reveal(service, ROUND_ID, "bob")
    # carol abstains; wait out the reveal deadline.
    clock.advance_to(REVEAL_DEADLINE)

    evidence = service.finalize("req-finalize", ROUND_ID)

    assert evidence["unrevealed_commitments"] == ["carol"]
    assert evidence["draw"]["eligible"] == ["alice", "bob"]
    assert evidence["draw"]["winner"] in {"alice", "bob"}
    # carol's random value never enters the public evidence.
    assert "carol" not in {r["participant_id"] for r in evidence["reveals"]}

    report = verify_evidence(evidence)
    assert report.valid, [c for c in report.checks if not c.ok]


def test_abstain_changes_outcome_relative_to_full_reveal(service, clock):
    """Demonstrates the bias lever: the seed (and possibly the winner) moves
    when a participant withholds their reveal."""
    make_round(service, round_id="full")
    commit_all(service, clock, round_id="full")
    for pid in ("alice", "bob", "carol"):
        reveal(service, "full", pid)
    full = service.finalize("req-f1", "full")

    clock.advance_to(1_700_000_000)
    make_round(service, round_id="abstain")
    commit_all(service, clock, round_id="abstain")
    reveal(service, "abstain", "alice")
    reveal(service, "abstain", "bob")
    clock.advance_to(REVEAL_DEADLINE)
    abstain = service.finalize("req-f2", "abstain")

    assert full["seed"] != abstain["seed"]
    assert abstain["draw"]["eligible"] != full["draw"]["eligible"]


def test_round_with_zero_reveals_is_undetermined(service, clock, audit):
    import pytest

    from commit_reveal.errors import ErrorCode, ProtocolError

    make_round(service)
    commit_all(service, clock)
    clock.advance_to(REVEAL_DEADLINE)

    with pytest.raises(ProtocolError) as excinfo:
        service.finalize("req-finalize", ROUND_ID)
    assert excinfo.value.code == ErrorCode.NO_VALID_REVEALS

    events = [e for e in audit.list_for_round(ROUND_ID) if e["event_type"] == "finalize"]
    assert events[-1]["outcome"] == "UNDETERMINED"
