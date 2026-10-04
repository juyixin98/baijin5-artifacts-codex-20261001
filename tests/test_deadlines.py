"""Deadline enforcement: the commitment set freezes at the commit deadline;
late commitments are rejected with LATE_COMMITMENT and never enter the set.
Late reveals and out-of-phase reveals are rejected too.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from commit_reveal.protocol import errors
from tests.conftest import (
    COMMIT_DEADLINE,
    REVEAL_DEADLINE,
    commit_all,
    synth_secret,
)
from tests.helpers import reference_impl as ref


def test_late_commitment_rejected(service, clock, round_id):
    commit_all(service, round_id, names=["alice", "bob"])
    clock.set(COMMIT_DEADLINE + timedelta(seconds=1))

    carol_commitment = ref.commitment(
        round_id, "carol",
        bytes.fromhex(synth_secret("carol", "value")),
        bytes.fromhex(synth_secret("carol", "salt")),
    )
    with pytest.raises(errors.LateCommitmentError) as excinfo:
        service.commit(round_id, "carol", carol_commitment, "req-commit-late")
    assert excinfo.value.category == "LATE_COMMITMENT"

    # The frozen set contains exactly the two on-time commitments.
    stored = service._store.list_commitments(round_id)
    assert sorted(c["participant_id"] for c in stored) == ["alice", "bob"]


def test_commitment_at_exact_deadline_rejected(service, clock, round_id):
    clock.set(COMMIT_DEADLINE)  # boundary: deadline itself is already closed
    commitment = ref.commitment(
        round_id, "alice",
        bytes.fromhex(synth_secret("alice", "value")),
        bytes.fromhex(synth_secret("alice", "salt")),
    )
    with pytest.raises(errors.LateCommitmentError):
        service.commit(round_id, "alice", commitment, "req-commit-boundary")


def test_reveal_before_freeze_rejected(service, clock, round_id):
    commit_all(service, round_id, names=["alice", "bob"])
    # clock still in commit phase
    with pytest.raises(errors.RevealPhaseNotOpenError) as excinfo:
        service.reveal(
            round_id, "alice", synth_secret("alice", "value"),
            synth_secret("alice", "salt"), "req-reveal-early",
        )
    assert excinfo.value.category == "REVEAL_PHASE_NOT_OPEN"


def test_late_reveal_rejected(service, clock, round_id):
    commit_all(service, round_id)
    clock.set(REVEAL_DEADLINE + timedelta(seconds=1))
    with pytest.raises(errors.LateRevealError) as excinfo:
        service.reveal(
            round_id, "alice", synth_secret("alice", "value"),
            synth_secret("alice", "salt"), "req-reveal-late",
        )
    assert excinfo.value.category == "LATE_REVEAL"


def test_finalize_before_reveal_deadline_rejected(service, clock, round_id):
    commit_all(service, round_id)
    with pytest.raises(errors.RoundNotFinalizableError):
        service.finalize(round_id, "req-finalize-early")


def test_duplicate_commitment_rejected(service, clock, round_id):
    commit_all(service, round_id, names=["alice"])
    again = ref.commitment(
        round_id, "alice",
        bytes.fromhex(synth_secret("alice", "value")),
        bytes.fromhex(synth_secret("alice", "salt")),
    )
    with pytest.raises(errors.DuplicateCommitmentError) as excinfo:
        service.commit(round_id, "alice", again, "req-commit-dup")
    assert excinfo.value.category == "DUPLICATE_COMMITMENT"
