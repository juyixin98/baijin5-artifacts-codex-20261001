"""Service-level tests: full pipeline, affected entities, locks, replay,
and distinguishable failure categories."""

import pytest

from er_backend.config import Settings
from er_backend.errors import (
    ConstraintConflictError,
    ERCategory,
    InputValidationError,
    ResourceExhaustedError,
)
from er_backend.index.store import Store
from er_backend.models import ConstraintSet, Lock
from er_backend.service import ResolutionService

from .fixtures import ORACLE_CONSTRAINTS, ORACLE_RECORDS, TOKEN_MAP, rec


def _partition(result):
    return frozenset(
        frozenset(c.record_ids) for c in result.clusters
    )


def test_resolve_produces_clusters_evidence_and_run_journal(service):
    service.ingest(ORACLE_RECORDS)
    service.set_constraints(ORACLE_CONSTRAINTS)
    result = service.resolve()

    assert _partition(result) == frozenset(
        [frozenset({"n1", "n2"}), frozenset({"n3", "n4"}), frozenset({"n5"})]
    )
    # First run: every record is affected.
    assert result.affected_record_ids == ["n1", "n2", "n3", "n4", "n5"]
    # Evidence cites the pairwise decisions behind each multi-record cluster.
    joined = "\n".join(
        line for lines in result.evidence.values() for line in lines
    )
    assert "n1 ~ n2" in joined
    assert "must_link" in joined

    # The run journal keeps run id, decisions with reasons, and assignment.
    run = service.store.load_run(result.run_id)
    assert run["status"] == "completed"
    assert run["decisions"], "decision log must be persisted for replay"
    assert all("reason" in d for d in run["decisions"])
    assert run["snapshot"]["records"], "input snapshot must be persisted"


def test_affected_entities_after_constraint_change(service):
    service.ingest(ORACLE_RECORDS)
    service.set_constraints(ORACLE_CONSTRAINTS)
    service.resolve()

    # Forbid n1~n2: their cluster splits, only they are affected.
    service.set_constraints(
        ConstraintSet(
            must_link=[("n3", "n4")],
            cannot_link=[("n1", "n4"), ("n1", "n2")],
        )
    )
    result = service.resolve()
    assert _partition(result) == frozenset(
        [
            frozenset({"n1"}),
            frozenset({"n2"}),
            frozenset({"n3", "n4"}),
            frozenset({"n5"}),
        ]
    )
    assert result.affected_record_ids == ["n1", "n2"]


def test_lock_survives_re_resolution_and_extends_cluster(service):
    service.ingest([rec("P", "Pine Labs"), rec("Q", "Pine Laboratories")])
    first = service.resolve()
    assert _partition(first) == frozenset(
        [frozenset({"P"}), frozenset({"Q"})]
    )

    # Human confirms P and Q are the same entity: lock the mapping.
    service.add_lock(Lock(lock_id="L1", record_ids=["P", "Q"], note="confirmed"))
    second = service.resolve()
    assert _partition(second) == frozenset([frozenset({"P", "Q"})])
    cluster = second.clusters[0]
    assert cluster.lock_ids == ["L1"]
    assert any("human-confirmed lock: L1" in l for l in second.evidence[cluster.cluster_id])

    # A new record matching P joins the locked cluster; P, Q, R are affected.
    service.ingest([rec("R", "Pine Labs Ltd")])
    third = service.resolve()
    assert _partition(third) == frozenset([frozenset({"P", "Q", "R"})])
    assert third.affected_record_ids == ["P", "Q", "R"]


def test_lock_contradicting_cannot_link_is_state_conflict(service):
    service.ingest([rec("P", "Pine Labs"), rec("Q", "Pine Laboratories")])
    service.set_constraints(ConstraintSet(cannot_link=[("P", "Q")]))
    service.add_lock(Lock(lock_id="L1", record_ids=["P", "Q"]))
    with pytest.raises(ConstraintConflictError) as excinfo:
        service.resolve()
    assert excinfo.value.category == ERCategory.STATE_CONFLICT


def test_conflicting_constraints_rejected_at_submission(service):
    service.ingest(ORACLE_RECORDS)
    with pytest.raises(ConstraintConflictError):
        service.set_constraints(
            ConstraintSet(must_link=[("n1", "n2")], cannot_link=[("n1", "n2")])
        )


def test_duplicate_record_ids_are_input_error(service):
    with pytest.raises(InputValidationError) as excinfo:
        service.ingest([rec("X", "Acme"), rec("X", "Acme Ltd")])
    assert excinfo.value.category == ERCategory.INPUT_ERROR


def test_candidate_budget_exceeded_is_resource_exhausted(tmp_path):
    settings = Settings(max_candidates=0)
    service = ResolutionService(
        Store(tmp_path / "exhausted.sqlite3"), settings, TOKEN_MAP
    )
    service.ingest([rec("A", "Acme Trading"), rec("B", "Acme Trading Ltd")])
    with pytest.raises(ResourceExhaustedError) as excinfo:
        service.resolve()
    assert excinfo.value.category == ERCategory.RESOURCE_EXHAUSTED


def test_replay_reproduces_recorded_assignment(service):
    service.ingest(ORACLE_RECORDS)
    service.set_constraints(ORACLE_CONSTRAINTS)
    result = service.resolve()
    replay = service.replay(result.run_id)
    assert replay["replayed"] is True
    assert replay["clusters"] == 3


def test_resolution_is_deterministic_across_runs(service):
    service.ingest(ORACLE_RECORDS)
    service.set_constraints(ORACLE_CONSTRAINTS)
    first = service.resolve()
    second = service.resolve()
    assert _partition(first) == _partition(second)
    # No state changed: nothing is affected by the second run.
    assert second.affected_record_ids == []
