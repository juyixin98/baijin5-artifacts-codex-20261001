"""Wrong-salt reveals must be rejected with COMMITMENT_MISMATCH, must not be
counted, and must leave the participant able to retry with the correct salt.
"""

from __future__ import annotations

import hashlib

import pytest

from commit_reveal.protocol import errors
from tests.conftest import (
    COMMIT_DEADLINE,
    commit_all,
    past_reveal_deadline,
    reveal_all,
    synth_secret,
)
from datetime import timedelta


def test_wrong_salt_rejected_and_not_counted(service, clock, round_id):
    commit_all(service, round_id)
    clock.set(COMMIT_DEADLINE + timedelta(minutes=1))

    wrong_salt = hashlib.sha256(b"fixture:salt:wrong").hexdigest()
    with pytest.raises(errors.CommitmentMismatchError) as excinfo:
        service.reveal(
            round_id, "alice", synth_secret("alice", "value"), wrong_salt,
            "req-reveal-bad",
        )
    assert excinfo.value.category == "COMMITMENT_MISMATCH"
    # The failed reveal was not recorded.
    assert service._store.get_reveal(round_id, "alice") is None

    # Correct reveal afterwards still succeeds (a typo is not a punishment).
    service.reveal(
        round_id, "alice", synth_secret("alice", "value"),
        synth_secret("alice", "salt"), "req-reveal-good",
    )
    assert service._store.get_reveal(round_id, "alice") is not None


def test_wrong_value_rejected(service, clock, round_id):
    commit_all(service, round_id)
    clock.set(COMMIT_DEADLINE + timedelta(minutes=1))
    wrong_value = hashlib.sha256(b"fixture:value:wrong").hexdigest()
    with pytest.raises(errors.CommitmentMismatchError):
        service.reveal(
            round_id, "bob", wrong_value, synth_secret("bob", "salt"),
            "req-reveal-bad-value",
        )


def test_rejection_is_audited_with_reason_and_redaction(
    service, clock, round_id
):
    commit_all(service, round_id)
    clock.set(COMMIT_DEADLINE + timedelta(minutes=1))
    wrong_salt = hashlib.sha256(b"fixture:salt:wrong").hexdigest()
    with pytest.raises(errors.CommitmentMismatchError):
        service.reveal(
            round_id, "alice", synth_secret("alice", "value"), wrong_salt,
            "req-reveal-audit",
        )

    audit_rows = service.list_audit(round_id)
    reject = [r for r in audit_rows if r["request_id"] == "req-reveal-audit"]
    assert len(reject) == 1
    assert reject[0]["decision"] == "REJECT"
    assert "does not match" in reject[0]["reason"]
    # Sensitive material must never appear verbatim in the audit trail.
    blob = str(audit_rows)
    assert synth_secret("alice", "value") not in blob
    assert wrong_salt not in blob
