"""Append-only evidence, restart recovery, duplicate ids and run limits."""

import pytest

from app.contracts import W0
from app.errors import (
    DuplicateHypothesisError,
    RunLimitReachedError,
    RunNotFoundError,
)
from app.store import SQLiteStore


def test_decision_rows_are_persisted_and_paged(tmp_path):
    db = str(tmp_path / "e.db")
    store = SQLiteStore(db)
    run = store.create_run(max_decisions=100)
    for i, p in enumerate([0.9, 0.9, 1e-6, 0.8], start=1):
        rec = store.append_decision(run.run_id, f"H{i}", p)
        assert rec["idx"] == i
        assert len(rec["row_hash"]) == 64
    rows = store.list_decisions(run.run_id)
    assert [r["hypothesis_id"] for r in rows] == ["H1", "H2", "H3", "H4"]
    assert [bool(r["rejected"]) for r in rows] == [False, False, True, False]
    page = store.list_decisions(run.run_id, limit=2, offset=2)
    assert [r["idx"] for r in page] == [3, 4]
    store.close()


def test_restart_recovers_hot_state_from_sqlite(tmp_path):
    db = str(tmp_path / "restart.db")
    store = SQLiteStore(db)
    run = store.create_run(max_decisions=100)
    store.append_decision(run.run_id, "H1", 1e-7)   # reject
    store.append_decision(run.run_id, "H2", 0.1)    # no
    store.append_decision(run.run_id, "H3", 2.5e-4)  # reject
    before = store.get_run(run.run_id)
    store.close()

    restarted = SQLiteStore(db)  # fresh process-like instance, no cache
    meta = restarted.get_run(run.run_id)
    assert meta.last_index == 3
    assert meta.tau == before.tau == 3
    assert meta.wealth == pytest.approx(before.wealth)
    assert meta.w_tau == pytest.approx(before.w_tau)
    assert meta.contract_fingerprint == before.contract_fingerprint
    # A 4th step after restart continues the exact same trajectory.
    rec = restarted.append_decision(run.run_id, "H4", 0.07)
    assert rec["threshold"] == pytest.approx(0.0010914232905232565, abs=1e-15)
    assert rec["rejected"] is False
    assert rec["prev_hash"] != "0" * 64
    restarted.close()


def test_duplicate_hypothesis_id_is_state_conflict_and_spends_nothing(tmp_path):
    store = SQLiteStore(":memory:")
    run = store.create_run()
    store.append_decision(run.run_id, "DUP", 0.4)
    with pytest.raises(DuplicateHypothesisError) as exc:
        store.append_decision(run.run_id, "DUP", 1e-9)
    assert exc.value.error_code.category.value == "STATE_CONFLICT"
    assert exc.value.error_code.http_status == 409
    # The later, tiny p-value must NOT retroactively create a rejection or
    # change any state: spent budget is irrevocable.
    meta = store.get_run(run.run_id)
    assert meta.last_index == 1
    assert meta.tau == 0
    assert meta.wealth == pytest.approx(W0 - store.list_decisions(
        run.run_id)[0]["threshold"])
    only = store.list_decisions(run.run_id)[0]
    assert only["p_value"] == 0.4
    assert bool(only["rejected"]) is False
    # A new id still advances normally after the rejected duplicate attempt.
    rec = store.append_decision(run.run_id, "OTHER", 1e-9)
    assert rec["idx"] == 2


def test_duplicate_detected_after_restart(tmp_path):
    db = str(tmp_path / "dup.db")
    store = SQLiteStore(db)
    run = store.create_run()
    store.append_decision(run.run_id, "SEEN", 0.3)
    store.close()
    again = SQLiteStore(db)
    with pytest.raises(DuplicateHypothesisError):
        again.append_decision(run.run_id, "SEEN", 0.3)
    again.close()


def test_run_limit_is_resource_exhausted(tmp_path):
    store = SQLiteStore(":memory:")
    run = store.create_run(max_decisions=3)
    for i in range(1, 4):
        store.append_decision(run.run_id, f"H{i}", 0.9)
    with pytest.raises(RunLimitReachedError) as exc:
        store.append_decision(run.run_id, "H4", 0.9)
    assert exc.value.error_code.category.value == "RESOURCE_EXHAUSTED"
    assert exc.value.error_code.http_status == 507
    assert store.get_run(run.run_id).last_index == 3


def test_unknown_run_is_not_found():
    store = SQLiteStore(":memory:")
    with pytest.raises(RunNotFoundError) as exc:
        store.append_decision("nope", "H1", 0.5)
    assert exc.value.error_code.category.value == "NOT_FOUND"
    assert exc.value.error_code.http_status == 404


def test_hash_chain_links_each_row_to_previous(tmp_path):
    store = SQLiteStore(":memory:")
    run = store.create_run()
    hashes = []
    for i, p in enumerate([1e-7, 0.5, 1e-5], start=1):
        hashes.append(store.append_decision(run.run_id, f"H{i}", p)["row_hash"])
    rows = store.all_decision_rows(run.run_id)
    assert rows[0]["prev_hash"] == "0" * 64
    assert [r["row_hash"] for r in rows] == hashes
    for prev, row in zip(hashes, rows[1:]):
        assert row["prev_hash"] == prev


def test_create_run_rejects_out_of_range_limits():
    store = SQLiteStore(":memory:")
    with pytest.raises(ValueError):
        store.create_run(max_decisions=0)
    with pytest.raises(ValueError):
        store.create_run(max_decisions=10 ** 9)
