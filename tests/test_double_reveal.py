"""A reveal counts exactly once: duplicates are rejected with
DUPLICATE_REVEAL and cannot alter the recorded value or the final seed.
"""

from __future__ import annotations

import pytest

from commit_reveal.protocol import errors
from tests.conftest import (
    commit_all,
    past_reveal_deadline,
    reveal_all,
    synth_secret,
)


def test_duplicate_reveal_rejected(service, clock, round_id):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id, names=["alice"])

    with pytest.raises(errors.DuplicateRevealError) as excinfo:
        service.reveal(
            round_id, "alice", synth_secret("alice", "value"),
            synth_secret("alice", "salt"), "req-reveal-dup",
        )
    assert excinfo.value.category == "DUPLICATE_REVEAL"
    assert len(service._store.list_reveals(round_id)) == 1


def test_duplicate_reveal_cannot_change_seed(service, clock, round_id):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id)
    past_reveal_deadline(clock)
    first = service.finalize(round_id, "req-finalize")

    # Any further reveal attempt after finalization is rejected (phase closed
    # takes precedence), and the stored result is untouched.
    with pytest.raises(errors.ProtocolError):
        service.reveal(
            round_id, "alice", synth_secret("alice", "value"),
            synth_secret("alice", "salt"), "req-reveal-post",
        )
    second = service.finalize(round_id, "req-finalize-again")
    assert second["seed_hex"] == first["seed_hex"]
    assert second["winner"] == first["winner"]
