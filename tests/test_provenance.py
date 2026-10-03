"""Provenance: runs, steps, replay, idempotency, and state conflicts."""

from __future__ import annotations

import pytest

from njtree import BuildParams, ProvenanceStore, TreeService
from njtree.errors import (
    ErrorCategory,
    InputValidationError,
    StateConflictError,
    UnknownRunError,
)
from njtree.models import NegativeBranchMode

from .conftest import load_fixture


@pytest.fixture
def service(tmp_path):
    return TreeService(ProvenanceStore(str(tmp_path / "runs.sqlite")))


def test_completed_run_records_steps_and_result(service):
    fx = load_fixture("additive4.json")
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    run = service.store.get_run(result.run_id)
    assert run["status"] == "completed"
    assert run["n_taxa"] == 4
    assert len(run["input_sha256"]) == 64
    steps = service.store.get_steps(result.run_id)
    assert len(steps) == 2  # n-2 join/root decisions for 4 taxa
    assert steps[0]["chosen_labels"] == ["A", "B"]
    assert steps[-1]["is_final"] is True
    stored = service.store.get_result(result.run_id)
    assert stored["newick"] == result.newick
    assert stored["leaf_map"] == result.leaf_map


def test_replay_confirms_determinism(service):
    fx = load_fixture("additive6.json")
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    report = service.replay(result.run_id)
    assert report.match is True
    assert report.differences == []


def test_idempotent_resubmission_returns_stored_result(service):
    fx = load_fixture("additive4.json")
    params = BuildParams(run_id="client-supplied-1")
    first = service.build_from_matrix(fx["labels"], fx["matrix"], params)
    second = service.build_from_matrix(fx["labels"], fx["matrix"], params)
    assert first.idempotent is False
    assert second.idempotent is True
    assert second.newick == first.newick
    assert second.run_id == "client-supplied-1"


def test_run_id_reuse_with_different_input_is_state_conflict(service):
    fx = load_fixture("additive4.json")
    service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams(run_id="dup"))
    other = load_fixture("negative_branch.json")
    with pytest.raises(StateConflictError) as excinfo:
        service.build_from_matrix(other["labels"], other["matrix"], BuildParams(run_id="dup"))
    assert excinfo.value.category is ErrorCategory.STATE_CONFLICT
    assert excinfo.value.run_id == "dup"


def test_failed_run_is_recorded_with_category(service):
    fx = load_fixture("negative_branch.json")
    params = BuildParams(negative_branch_mode=NegativeBranchMode.ERROR, run_id="will-fail")
    with pytest.raises(Exception):
        service.build_from_matrix(fx["labels"], fx["matrix"], params)
    run = service.store.get_run("will-fail")
    assert run["status"] == "failed"
    assert run["error_category"] == ErrorCategory.COMPUTATION_FAILURE.value
    assert "negative branch" in run["error_message"]
    # A failed run cannot be silently retried under the same id.
    with pytest.raises(StateConflictError):
        service.build_from_matrix(fx["labels"], fx["matrix"], params)
    # Replay of a failed run reports instead of crashing.
    report = service.replay("will-fail")
    assert report.match is False


def test_invalid_input_never_creates_a_run(service):
    with pytest.raises(InputValidationError):
        service.build_from_matrix(["A", "B", "C"], [[0, 1, 2], [1, 0, 3], [2, 3, 1]],
                                  BuildParams(run_id="not-created"))
    assert service.store.get_run("not-created") is None


def test_unknown_run_raises_unknown_run_error(service):
    with pytest.raises(UnknownRunError):
        service.replay("no-such-run")
