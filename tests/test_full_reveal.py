"""Full-reveal happy path: every participant commits and reveals, the round
finalizes, and the result matches the independent reference implementation
bit-for-bit. Re-running finalize is an idempotent replay.
"""

from __future__ import annotations

import json
from pathlib import Path

from commit_reveal.state import store as store_mod
from commit_reveal.verify.independent import verify_evidence
from tests.conftest import (
    PARTICIPANTS,
    commit_all,
    past_reveal_deadline,
    reveal_all,
)
from tests.helpers import reference_impl as ref

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "reference_vectors.json").read_text()
)


def test_full_reveal_finalizes_with_reference_result(service, clock, round_id):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id)
    past_reveal_deadline(clock)

    result = service.finalize(round_id, "req-finalize")

    assert result["seed_hex"] == VECTORS["full_reveal"]["seed"]
    assert result["ranking"] == VECTORS["full_reveal"]["ranking"]
    assert result["winner"] == VECTORS["full_reveal"]["winner"]
    # Cross-check against the live reference implementation too.
    expected_ranking = ref.ranking(
        list(PARTICIPANTS), bytes.fromhex(result["seed_hex"])
    )
    assert result["ranking"] == expected_ranking
    assert service.get_round(round_id)["status"] == store_mod.STATUS_FINALIZED


def test_full_reveal_evidence_passes_independent_verification(
    service, clock, round_id
):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id)
    past_reveal_deadline(clock)
    service.finalize(round_id, "req-finalize")

    evidence = service.get_evidence(round_id)
    checks = verify_evidence(evidence)
    assert checks, "verifier must emit at least one check"
    assert all(c.passed for c in checks), [
        (c.name, c.expected, c.actual) for c in checks if not c.passed
    ]
    assert evidence["bias_warning"] is False
    assert evidence["unrevealed_commitments"] == []


def test_finalize_is_idempotent_replay(service, clock, round_id):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id)
    past_reveal_deadline(clock)

    first = service.finalize(round_id, "req-finalize-1")
    second = service.finalize(round_id, "req-finalize-2")

    assert first == second
    assert first["seed_hex"] == second["seed_hex"]
    assert first["winner"] == second["winner"]
