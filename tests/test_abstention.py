"""Selective abstention: a participant who commits but never reveals is
excluded from the seed. The protocol finalizes over the revealed subset,
flags the bias risk in the public evidence, and the outcome provably
differs from the full-reveal outcome — this is the documented limitation
that makes the protocol unsuitable for real-money draws.
"""

from __future__ import annotations

import json
from pathlib import Path

from commit_reveal.verify.independent import verify_evidence
from tests.conftest import commit_all, past_reveal_deadline, reveal_all

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "reference_vectors.json").read_text()
)


def test_abstention_excludes_unrevealed_and_flags_bias(service, clock, round_id):
    commit_all(service, round_id)  # alice, bob, carol all commit
    reveal_all(service, clock, round_id, names=["alice", "bob"])  # carol abstains
    past_reveal_deadline(clock)

    result = service.finalize(round_id, "req-finalize")
    evidence = result["evidence"]

    # Result matches the frozen abstention vectors from the reference impl.
    assert result["seed_hex"] == VECTORS["abstain_carol"]["seed"]
    assert result["ranking"] == VECTORS["abstain_carol"]["ranking"]
    assert result["winner"] == VECTORS["abstain_carol"]["winner"]

    # The abstainer is named in the public evidence, with the bias warning.
    assert evidence["unrevealed_commitments"] == ["carol"]
    assert evidence["bias_warning"] is True
    assert "selective" in evidence["bias_note"]
    assert evidence["draw"]["eligible"] == ["alice", "bob"]
    assert "carol" not in evidence["draw"]["ranking"]

    # Evidence still verifies independently: the abstainer's commitment is
    # part of the frozen set, only their input is excluded from the seed.
    checks = verify_evidence(evidence)
    assert all(c.passed for c in checks)


def test_abstention_changes_outcome_vs_full_reveal(service, clock):
    """Demonstrates the bias lever: withholding one reveal flips the winner.

    Uses the frozen ``flip_demo`` vectors (5 participants): the reference
    implementation shows carol's abstention moves the winner bob -> dave.
    """
    demo = VECTORS["flip_demo"]
    rid = demo["round_id"]
    service.create_round(
        participants=demo["participants"],
        commit_deadline="2026-01-01T01:00:00+00:00",
        reveal_deadline="2026-01-01T02:00:00+00:00",
        min_reveals=2,
        request_id="req-create-flip",
        round_id=rid,
    )
    commit_all(service, rid, names=demo["participants"])
    reveal_all(service, clock, rid,
               names=[n for n in demo["participants"] if n != "carol"])
    past_reveal_deadline(clock)
    abstained = service.finalize(rid, "req-finalize-flip")

    # Seed and winner match the frozen abstention vectors...
    assert abstained["seed_hex"] == demo["abstain_carol"]["seed"]
    assert abstained["winner"] == demo["abstain_carol"]["winner"]
    # ...and the winner provably differs from the full-reveal counterfactual.
    assert abstained["seed_hex"] != demo["full"]["seed"]
    assert abstained["winner"] != demo["full"]["winner"]


def test_abstention_audit_records_undecidable_nothing_here(
    service, clock, round_id
):
    """Finalize with unrevealed commitments is ACCEPTED but names them."""
    commit_all(service, round_id)
    reveal_all(service, clock, round_id, names=["alice", "bob"])
    past_reveal_deadline(clock)
    service.finalize(round_id, "req-finalize")

    rows = [r for r in service.list_audit(round_id) if r["event"] == "finalize"]
    assert rows[-1]["decision"] == "ACCEPT"
    assert "unrevealed" in rows[-1]["reason"]
