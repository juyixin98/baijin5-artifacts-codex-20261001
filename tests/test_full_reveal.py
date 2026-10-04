"""Full-reveal happy path and replay consistency."""

from commit_reveal.services.round_service import STATUS_FINALIZED
from commit_reveal.verify.verifier import verify_evidence

from tests.conftest import (
    COMMIT_DEADLINE,
    PARTICIPANTS,
    ROUND_ID,
    commit_all,
    make_round,
    reveal,
)


def _run_full_round(service, clock, round_id=ROUND_ID):
    make_round(service, round_id)
    commit_all(service, clock, round_id)
    for pid in PARTICIPANTS:
        reveal(service, round_id, pid)
    return service.finalize("req-finalize", round_id)


def test_full_reveal_finalizes_with_verifiable_evidence(service, clock):
    evidence = _run_full_round(service, clock)

    assert evidence["round_id"] == ROUND_ID
    assert evidence["unrevealed_commitments"] == []
    assert evidence["draw"]["eligible"] == sorted(PARTICIPANTS)
    assert evidence["draw"]["winner"] in PARTICIPANTS
    assert len(evidence["seed"]) == 64

    report = verify_evidence(evidence)
    assert report.valid, [c for c in report.checks if not c.ok]

    view = service.get_round_view(ROUND_ID)
    assert view["status"] == STATUS_FINALIZED
    assert view["result"]["winner"] == evidence["draw"]["winner"]


def test_replay_is_deterministic(service, clock):
    """Same inputs on two fresh service instances: identical evidence."""
    from commit_reveal.services.round_service import RoundService
    from commit_reveal.state.audit import AuditLog
    from commit_reveal.state.db import connect
    from commit_reveal.state.repository import Repository
    from tests.conftest import FakeClock

    first = _run_full_round(service, clock, round_id="round-a")

    fresh_clock = FakeClock()
    conn = connect(":memory:")
    fresh_service = RoundService(Repository(conn), AuditLog(conn), clock=fresh_clock)
    second = _run_full_round(fresh_service, fresh_clock, round_id="round-a")

    assert first == second
    assert first["seed"] == second["seed"]
    assert first["draw"]["winner"] == second["draw"]["winner"]


def test_same_reveals_different_round_id_give_different_seed(service, clock):
    first = _run_full_round(service, clock, round_id="round-x")
    clock.advance_to(1_700_000_000)
    second = _run_full_round(service, clock, round_id="round-y")
    assert first["seed"] != second["seed"]


def test_early_finalize_rejected_until_all_reveal(service, clock):
    import pytest

    from commit_reveal.errors import ErrorCode, ProtocolError

    make_round(service)
    commit_all(service, clock)
    reveal(service, ROUND_ID, "alice")
    reveal(service, ROUND_ID, "bob")
    # carol has not revealed and the reveal deadline has not passed.
    with pytest.raises(ProtocolError) as excinfo:
        service.finalize("req-finalize-early", ROUND_ID)
    assert excinfo.value.code == ErrorCode.ROUND_NOT_FINALIZABLE

    # Once everyone reveals, finalization succeeds before the deadline.
    reveal(service, ROUND_ID, "carol")
    evidence = service.finalize("req-finalize", ROUND_ID)
    assert evidence["draw"]["winner"] in PARTICIPANTS
