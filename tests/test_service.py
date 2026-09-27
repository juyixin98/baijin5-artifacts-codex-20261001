"""Service-layer and SQLite persistence tests, incl. cross-instance resume."""

from __future__ import annotations

from tests.conftest import CORPUS_ABC, CORPUS_EMPTY_DUP
from tests.brute_force import closed_itemsets as oracle_closed
from tests.brute_force import maximal_itemsets as oracle_maximal
from app.config import Settings
from app.repository import Repository
from app.service import FimService


def _ingest(service, rows):
    dataset_id, stats = service.ingest_dataset(None, rows)
    return dataset_id


def test_ingest_stats_reflect_empty_and_duplicate_copies(service):
    dataset_id, stats = service.ingest_dataset(None, CORPUS_EMPTY_DUP)
    assert stats.transaction_count == 4
    assert stats.distinct_item_count == 1
    assert stats.empty_transaction_count == 2
    # e1,e2 are identical empty; x1,x2 identical {x}: 4 copies participate.
    assert stats.duplicate_transaction_count == 4
    record = service.get_dataset_or_raise(dataset_id)
    assert record.content_hash  # deterministic identity is populated


def test_repeated_item_collapses_through_full_pipeline(service):
    dataset_id, _ = service.ingest_dataset(None, [
        {"tid": "t1", "items": ["a", "a", "a"]},
        {"tid": "t2", "items": ["a"]},
    ])
    record, chunk = service.create_job(dataset_id, 2, None, "req-1")
    assert record.complete is True
    assert set(record.closed) == {frozenset("a")}
    assert record.closed[frozenset("a")] == 2
    assert chunk >= 1


def test_partial_run_flags_maximals_uncertain_then_completes(service):
    dataset_id = _ingest(service, CORPUS_ABC)
    record, chunk = service.create_job(dataset_id, 1, 0, "req-partial")
    assert record.complete is False
    assert record.status == "running"
    # Budget 0: only the root evaluation; maximals withheld and flagged.
    assert record.evaluations_used == 1
    assert any("budget=0" in note for note in record.notes)
    assert any("PARTIAL" in note for note in record.notes)

    # Resume with a tiny budget: still partial, closed results keep growing and
    # maximals are still withheld (uncertain).
    record, _ = service.resume_job(record.job_id, 2, "req-resume-1")
    assert record.complete is False

    # Finish it.
    record, _ = service.resume_job(record.job_id, 10_000, "req-resume-2")
    assert record.complete is True
    assert record.status == "complete"
    assert record.closed == oracle_closed(CORPUS_ABC, 1)
    assert set(__import__("app.miner", fromlist=["derive_maximal"]).derive_maximal(record.closed)) == set(
        oracle_maximal(CORPUS_ABC, 1)
    )
    assert record.notes == [] or all("PARTIAL" not in n for n in record.notes)


def test_resume_accumulates_without_losing_or_duplicating(service):
    dataset_id = _ingest(service, CORPUS_ABC)
    record, _ = service.create_job(dataset_id, 2, 1, "req-a")
    total_evals = record.evaluations_used
    seen = set(record.closed)
    while not record.complete:
        record, chunk_evals = service.resume_job(record.job_id, 1, "req-b")
        # budget=1 -> at most one evaluation; a chunk that only pops exhausted
        # frames at the tail of the search can finish with zero evaluations.
        assert 0 <= chunk_evals <= 1
        total_evals += chunk_evals
        current = set(record.closed)
        # Persisted closed set is strictly cumulative, never rewritten/dropped.
        assert seen <= current
        seen = current
    assert record.closed == oracle_closed(CORPUS_ABC, 2)
    assert record.evaluations_used == total_evals


def test_resume_state_survives_fresh_repository_instance(settings):
    service1 = FimService(Repository(settings.database_path), settings)
    dataset_id = _ingest(service1, CORPUS_ABC)
    record, _ = service1.create_job(dataset_id, 1, 2, "req-persist")
    assert record.complete is False
    job_id = record.job_id
    partial_closed = dict(record.closed)

    # Simulate a process restart: brand new Repository over the same DB file.
    service2 = FimService(Repository(settings.database_path), settings)
    resumed, _ = service2.resume_job(job_id, 10_000, "req-after-restart")
    assert resumed.complete is True
    assert set(partial_closed) <= set(resumed.closed)
    assert resumed.closed == oracle_closed(CORPUS_ABC, 1)
    assert resumed.request_id == "req-after-restart"  # latest request identity


def test_resuming_complete_job_is_a_noop(service):
    dataset_id = _ingest(service, CORPUS_ABC)
    record, _ = service.create_job(dataset_id, 1, None, "req-done")
    assert record.complete is True
    final_evals = record.evaluations_used
    again, chunk = service.resume_job(record.job_id, 100, "req-again")
    assert again.complete is True
    assert chunk == 0
    assert again.evaluations_used == final_evals


def test_min_support_above_transaction_count_is_trivially_complete(service):
    dataset_id = _ingest(service, CORPUS_ABC)
    record, _ = service.create_job(dataset_id, 99, None, "req-edge")
    assert record.complete is True
    assert record.closed == {}
    assert any("no frequent itemset" in note for note in record.notes)


def test_budget_is_clamped_to_configured_maximum(settings):
    service = FimService(Repository(settings.database_path), settings)
    dataset_id = _ingest(service, CORPUS_ABC)
    record, _ = service.create_job(dataset_id, 1, settings.max_budget_nodes + 50, "req-clamp")
    assert any("clamped" in note for note in record.notes)


def test_missing_dataset_and_job_are_named_failures(service):
    import pytest
    from app.service import NotFoundError

    with pytest.raises(NotFoundError):
        service.create_job("does-not-exist", 1, None, "req-miss")
    with pytest.raises(NotFoundError):
        service.resume_job("no-such-job", None, "req-miss2")
    with pytest.raises(NotFoundError):
        service.get_job_or_raise("no-such-job")


def test_serialised_payload_is_explainable(service):
    dataset_id = _ingest(service, CORPUS_ABC)
    record, chunk = service.create_job(dataset_id, 2, None, "req-ser")
    payload = __import__("app.service", fromlist=["serialise_job"]).serialise_job(record, chunk_evals=chunk)
    # Every result is tied to request/dataset identity and engine version.
    assert payload["request_id"] == "req-ser"
    assert payload["dataset_id"] == dataset_id
    assert len(payload["dataset_hash"]) == 12
    assert payload["engine_version"]
    assert payload["min_support"] == 2
    assert payload["maximal_results_certain"] is True
    assert payload["chunk"]["evaluations_in_chunk"] == chunk
    # Closed (7) contains the three maximals; counts make the distinction visible.
    assert len(payload["closed_itemsets"]) == 7
    assert {tuple(x["itemset"]) for x in payload["maximal_itemsets"]} == {
        ("a", "b"),
        ("a", "c"),
        ("b", "c"),
    }
    # Supports are attached to every itemset.
    for entry in payload["closed_itemsets"]:
        assert isinstance(entry["support"], int) and entry["support"] >= 2
