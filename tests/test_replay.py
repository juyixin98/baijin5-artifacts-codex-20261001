"""Replay consistency: evidence produced by one service instance must
recompute identically from a cold start (fresh service over the same DB, and
fresh service over a fresh DB with the same inputs), and the standalone
verifier CLI must agree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from commit_reveal.state.audit import Audit
from commit_reveal.state.service import RoundService
from commit_reveal.state.store import Store
from commit_reveal.verify.independent import verify_evidence
from tests.conftest import T0, commit_all, past_reveal_deadline, reveal_all


def test_replay_from_same_database(service, clock, round_id, store):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id)
    past_reveal_deadline(clock)
    original = service.finalize(round_id, "req-finalize")

    # A brand-new service instance over the same DB replays identically.
    reloaded = RoundService(store, clock, Audit(store, clock))
    replayed = reloaded.finalize(round_id, "req-finalize-reloaded")
    assert replayed["seed_hex"] == original["seed_hex"]
    assert replayed["winner"] == original["winner"]
    assert replayed["evidence"] == original["evidence"]


def test_replay_from_scratch_same_inputs(service, clock, round_id, tmp_path):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id)
    past_reveal_deadline(clock)
    original = service.finalize(round_id, "req-finalize")

    # Independent run: fresh DB, same inputs, same timeline.
    clock.set(T0)
    store2 = Store(tmp_path / "replay.db")
    service2 = RoundService(store2, clock, Audit(store2, clock))
    service2.create_round(
        participants=["alice", "bob", "carol"],
        commit_deadline="2026-01-01T01:00:00+00:00",
        reveal_deadline="2026-01-01T02:00:00+00:00",
        min_reveals=2,
        request_id="req-create-2",
        round_id=round_id,
    )
    commit_all(service2, round_id)
    reveal_all(service2, clock, round_id)
    past_reveal_deadline(clock)
    rerun = service2.finalize(round_id, "req-finalize-2")
    store2.close()

    assert rerun["seed_hex"] == original["seed_hex"]
    assert rerun["ranking"] == original["ranking"]
    assert rerun["winner"] == original["winner"]


def test_verifier_cli_agrees(service, clock, round_id, tmp_path):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id)
    past_reveal_deadline(clock)
    service.finalize(round_id, "req-finalize")
    evidence = service.get_evidence(round_id)

    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence))
    src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
    env = {**os.environ, "PYTHONPATH": os.path.abspath(src_dir)}
    proc = subprocess.run(
        [sys.executable, "-m", "commit_reveal.verify.independent",
         str(evidence_path)],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in proc.stdout


def test_tampered_evidence_fails_verification(service, clock, round_id):
    commit_all(service, round_id)
    reveal_all(service, clock, round_id)
    past_reveal_deadline(clock)
    service.finalize(round_id, "req-finalize")
    evidence = service.get_evidence(round_id)

    evidence["draw"]["winner"] = (
        "bob" if evidence["draw"]["winner"] != "bob" else "alice"
    )
    checks = verify_evidence(evidence)
    assert any(not c.passed for c in checks)
