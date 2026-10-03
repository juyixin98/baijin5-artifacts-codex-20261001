"""Run log tests: run ids, intermediate states, replay, JSONL sink."""

import json

import pytest

from fir_backend.contracts import EstimateParams
from fir_backend.errors import InputValidationError
from fir_backend.estimator import estimate_fir
from fir_backend.fixtures import make_fixture
from fir_backend.runlog import RunLogger, file_sink, replay


def _params():
    return EstimateParams(model_order=8, delay=0, regularization=1e-3, holdout_fraction=0.25)


def test_run_log_captures_intermediate_states_under_one_run_id():
    logger = RunLogger()
    fixture = make_fixture("noisy")
    result = estimate_fir(
        fixture.excitation, fixture.response, _params(), logger=logger
    )
    events = [r.event for r in logger.for_run(result.run_id)]
    assert events == [
        "inputs_validated",
        "split",
        "solve_diagnostics",
        "metrics",
        "estimate_complete",
    ]
    # Every record for this run carries the same run id.
    assert {r.run_id for r in logger.records} == {result.run_id}


def test_solve_diagnostics_record_rank_and_regularization():
    logger = RunLogger()
    fixture = make_fixture("narrowband")
    result = estimate_fir(
        fixture.excitation, fixture.response, _params(), logger=logger
    )
    diag_record = next(r for r in logger.records if r.event == "solve_diagnostics")
    assert diag_record.payload["effective_rank"] < 8
    assert diag_record.payload["regularization"] == 1e-3
    assert diag_record.payload["identifiable"] is False
    assert diag_record.payload["unidentifiable_reasons"]
    metrics = next(r for r in logger.records if r.event == "metrics")
    assert metrics.payload["train_rmse"] > 0
    assert metrics.payload["holdout_rmse"] > 0


def test_validation_failure_logged_with_input_category():
    logger = RunLogger()
    with pytest.raises(InputValidationError):
        estimate_fir([1.0, 2.0], [1.0], _params(), logger=logger)
    failures = [r for r in logger.records if r.event == "validation_failed"]
    assert len(failures) == 1
    assert failures[0].payload["category"] == "input_error"


def test_replay_returns_ordered_payloads():
    logger = RunLogger()
    fixture = make_fixture("clean")
    result = estimate_fir(
        fixture.excitation, fixture.response, _params(), logger=logger
    )
    entries = replay(logger, result.run_id)
    assert [e["event"] for e in entries][0] == "inputs_validated"
    assert entries[-1]["event"] == "estimate_complete"


def test_file_sink_writes_parseable_jsonl(tmp_path):
    path = tmp_path / "run_log.jsonl"
    logger = RunLogger(sink=file_sink(str(path)))
    fixture = make_fixture("clean")
    result = estimate_fir(
        fixture.excitation, fixture.response, _params(), logger=logger
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 5
    for line in lines:
        record = json.loads(line)
        assert record["run_id"] == result.run_id
        assert "ts" in record and "event" in record
