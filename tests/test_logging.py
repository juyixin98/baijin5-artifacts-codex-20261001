"""Run logs must carry run ids, key intermediate states, and decision rationale."""

from __future__ import annotations

import logging

import pytest

from njtree import BuildParams, TreeService
from njtree.models import NegativeBranchMode

from .conftest import load_fixture


@pytest.fixture
def log_capture(caplog):
    with caplog.at_level(logging.INFO, logger="njtree"):
        yield caplog


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


def test_join_decisions_logged_with_run_id_and_rationale(log_capture):
    fx = load_fixture("additive4.json")
    service = TreeService()
    result = service.build_from_matrix(
        fx["labels"], fx["matrix"], BuildParams(run_id="log-run-1"))
    messages = _messages(log_capture)
    join_lines = [m for m in messages if "join" in m]
    assert join_lines, "expected join decision logs"
    for line in join_lines:
        assert "run=log-run-1" in line
        assert "q=" in line and "ties=" in line
        assert "tie_break=lexicographic_node_id" in line
    # The known 2-way tie of additive4 step 0 is visible in the log.
    assert any("ties=2" in m for m in join_lines)
    # Completion line carries the residual summary for postmortem.
    assert any("completed" in m and "total_residual=" in m for m in messages)


def test_negative_branch_events_and_failure_are_logged(log_capture):
    fx = load_fixture("negative_branch.json")
    service = TreeService()
    service.build_from_matrix(
        fx["labels"], fx["matrix"],
        BuildParams(negative_branch_mode=NegativeBranchMode.CLAMP, run_id="log-run-2"))
    warnings = [m for m in _messages(log_capture) if "negative branch" in m]
    assert len(warnings) == 2
    assert all("run=log-run-2" in m and "mode=clamp" in m for m in warnings)

    with pytest.raises(Exception):
        service.build_from_matrix(
            fx["labels"], fx["matrix"],
            BuildParams(negative_branch_mode=NegativeBranchMode.ERROR, run_id="log-run-3"))
    failures = [m for m in _messages(log_capture)
                if "run=log-run-3" in m and "category=computation_failure" in m]
    assert failures, "failure log must carry run id and error category"
