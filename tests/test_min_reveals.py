"""Below min_reveals the round cannot draw: it aborts explicitly, records an
UNDECIDABLE audit decision, and the evidence carries no seed or winner.
"""

from __future__ import annotations

from commit_reveal.state import store as store_mod
from commit_reveal.verify.independent import verify_evidence
from tests.conftest import commit_all, past_reveal_deadline, reveal_all


def test_insufficient_reveals_aborts_round(service, clock, round_id):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id, names=["alice"])  # min_reveals=2
    past_reveal_deadline(clock)

    result = service.finalize(round_id, "req-finalize")

    assert result["winner"] is None
    assert result["seed_hex"] is None
    assert result["evidence"]["aborted"] is True
    assert service.get_round(round_id)["status"] == store_mod.STATUS_ABORTED

    rows = [r for r in service.list_audit(round_id) if r["event"] == "finalize"]
    assert rows[-1]["decision"] == "UNDECIDABLE"
    assert "min_reveals" in rows[-1]["reason"]

    # Aborted evidence is still self-consistent for the verifier.
    checks = verify_evidence(result["evidence"])
    assert all(c.passed for c in checks)


def test_aborted_round_replays_consistently(service, clock, round_id):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id, names=["alice"])
    past_reveal_deadline(clock)
    first = service.finalize(round_id, "req-finalize")
    second = service.finalize(round_id, "req-finalize-2")
    assert first == second
