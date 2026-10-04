"""Shared fixtures: fake clock, in-memory service, deterministic participants."""

from __future__ import annotations

import pytest

from commit_reveal.crypto.commitment import compute_commitment
from commit_reveal.services.round_service import RoundService
from commit_reveal.state.audit import AuditLog
from commit_reveal.state.db import connect
from commit_reveal.state.repository import Repository

T0 = 1_700_000_000
COMMIT_DEADLINE = T0 + 100
REVEAL_DEADLINE = T0 + 200

PARTICIPANTS = ["alice", "bob", "carol"]

# Deterministic local fixture secrets (hex). Never reused across participants.
SECRETS = {
    "alice": {"random_value": "aa" * 32, "salt": "a1" * 16},
    "bob": {"random_value": "bb" * 32, "salt": "b2" * 16},
    "carol": {"random_value": "cc" * 32, "salt": "c3" * 16},
}

ROUND_ID = "round-1"


class FakeClock:
    def __init__(self, now: int = T0):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance_to(self, ts: int) -> None:
        self.now = ts


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(clock):
    conn = connect(":memory:")
    return RoundService(Repository(conn), AuditLog(conn), clock=clock)


@pytest.fixture
def audit(service) -> AuditLog:
    return service._audit  # tests inspect the audit trail directly


def commitment_for(round_id: str, participant_id: str) -> str:
    s = SECRETS[participant_id]
    return compute_commitment(
        round_id, participant_id,
        bytes.fromhex(s["random_value"]), bytes.fromhex(s["salt"]),
    )


def make_round(service, round_id: str = ROUND_ID, participants=PARTICIPANTS) -> str:
    service.create_round(
        "req-create", round_id, participants, COMMIT_DEADLINE, REVEAL_DEADLINE
    )
    return round_id


def commit_all(service, clock, round_id: str = ROUND_ID,
               participants=PARTICIPANTS) -> None:
    for pid in participants:
        service.commit(f"req-commit-{pid}", round_id, pid, commitment_for(round_id, pid))
    clock.advance_to(COMMIT_DEADLINE)  # freeze the commit set


def reveal(service, round_id: str, pid: str, salt: str | None = None) -> None:
    s = SECRETS[pid]
    service.reveal(f"req-reveal-{pid}", round_id, pid,
                   s["random_value"], salt if salt is not None else s["salt"])
