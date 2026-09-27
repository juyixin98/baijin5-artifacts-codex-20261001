"""Run-logger tests: replay records, intermediate state, failure category."""

from __future__ import annotations

from defeasible.errors import ResourceLimitError
from defeasible.logging import RunLogger
from defeasible.language import Term, Theory
from defeasible.engine import Engine


def _run(logger: RunLogger) -> str:
    from defeasible.service import ReasoningService
    from defeasible.storage import EvidenceStore

    store = EvidenceStore(":memory:")
    store.ensure_case("birds")
    t = Theory()
    t.add_rule("rb", "default", ["bird(X)"], "flies(X)")
    store.save_theory("birds", t.to_dict())
    store.add_evidence("birds", [Term.parse("bird(tweety)")])
    svc = ReasoningService(store, Engine(), logger)
    out = svc.query("birds", "flies(tweety)")
    return out["run_id"]


def test_logger_records_start_intermediate_result_in_memory() -> None:
    logger = RunLogger(":memory:")
    run_id = _run(logger)
    records = logger.records_for(run_id)
    events = [r["event"] for r in records]
    assert events == ["start", "intermediate", "result"]

    intermediate = records[1]
    assert intermediate["ground_rule_count"] >= 1
    assert "status_counts" in intermediate
    assert intermediate["candidate_literals"] >= 1

    result = records[2]
    assert result["status"] == "proved"
    assert result["chain_counts"]["support"] >= 1
    assert result["reasons"], "judgement reason must be recorded"


def test_run_id_is_unique_and_replayable() -> None:
    logger = RunLogger(":memory:")
    id1 = _run(logger)
    id2 = _run(logger)
    assert id1 != id2
    assert len(logger.records_for(id1)) == 3
    assert len(logger.records_for(id2)) == 3


def test_error_event_keeps_failure_category(tmp_path) -> None:
    log_path = str(tmp_path / "runs.jsonl")
    logger = RunLogger(log_path)
    logger.log_start("run-xyz", "c", "q(t)")
    logger.log_error(
        "run-xyz",
        ResourceLimitError("too big", details={"limit": 1}),
    )
    records = logger.records_for("run-xyz")
    assert records[1]["event"] == "error"
    assert records[1]["error_code"] == "resource_exhausted"
    # replay from the file on disk
    assert RunLogger(log_path).records_for("run-xyz")[1]["error_code"] == (
        "resource_exhausted"
    )


def test_torn_tail_line_does_not_destroy_history(tmp_path) -> None:
    log_path = tmp_path / "runs.jsonl"
    good = RunLogger(str(log_path))
    rid = _run(good)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("{broken json\n")
    records = RunLogger(str(log_path)).records_for(rid)
    assert [r["event"] for r in records] == ["start", "intermediate", "result"]
