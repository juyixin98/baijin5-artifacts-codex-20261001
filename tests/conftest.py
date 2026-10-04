"""Shared fixtures: manual clock, throwaway SQLite store, synthetic actors.

All participants, values and salts are synthetic and derived deterministically
from participant names (SHA-256), so test runs are reproducible without any
external data.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from commit_reveal.clock import ManualClock, to_iso
from commit_reveal.state.audit import Audit
from commit_reveal.state.service import RoundService
from commit_reveal.state.store import Store
from tests.helpers import reference_impl as ref

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
COMMIT_DEADLINE = T0 + timedelta(hours=1)
REVEAL_DEADLINE = T0 + timedelta(hours=2)

PARTICIPANTS = ["alice", "bob", "carol"]


def synth_secret(name: str, kind: str) -> str:
    """Deterministic synthetic 32-byte value/salt for a participant."""
    return hashlib.sha256(f"fixture:{kind}:{name}".encode()).hexdigest()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def service(store, clock) -> RoundService:
    return RoundService(store, clock, Audit(store, clock))


@pytest.fixture
def round_id(service) -> str:
    rnd = service.create_round(
        participants=list(PARTICIPANTS),
        commit_deadline=to_iso(COMMIT_DEADLINE),
        reveal_deadline=to_iso(REVEAL_DEADLINE),
        min_reveals=2,
        request_id="req-create",
        round_id="round-test",
    )
    return rnd["round_id"]


def commit_all(service: RoundService, round_id: str, names=PARTICIPANTS) -> None:
    for name in names:
        commitment = ref.commitment(
            round_id,
            name,
            bytes.fromhex(synth_secret(name, "value")),
            bytes.fromhex(synth_secret(name, "salt")),
        )
        service.commit(round_id, name, commitment, f"req-commit-{name}")


def reveal_all(service: RoundService, clock: ManualClock, round_id: str,
               names=PARTICIPANTS) -> None:
    clock.set(COMMIT_DEADLINE + timedelta(minutes=1))
    for name in names:
        service.reveal(
            round_id,
            name,
            synth_secret(name, "value"),
            synth_secret(name, "salt"),
            f"req-reveal-{name}",
        )


def past_reveal_deadline(clock: ManualClock) -> None:
    clock.set(REVEAL_DEADLINE + timedelta(minutes=1))
