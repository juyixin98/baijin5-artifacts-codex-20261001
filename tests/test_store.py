"""Tests for SQLite persistence, the vertical SQL index and serialized jobs."""

from __future__ import annotations

import pytest

from cfim.corpus import normalize_corpus
from cfim.errors import DomainError, ErrorCode
from cfim.kernel import advance, build_vertical_database, initial_state
from tests.oracle import mine_reference


def _make(store, name, transactions):
    return store.create_corpus(normalize_corpus(name, transactions, _settings(store)))


def _settings(store):
    # Minimal settings stand-in: corpus normalization only reads limits.
    from cfim.config import Settings

    return Settings(
        db_path=store.db_path,
        log_level="DEBUG",
        default_budget=10,
        max_transactions=10_000,
        max_items_per_transaction=256,
        max_item_length=128,
        max_corpus_name_length=200,
        max_advance_budget=100_000,
    )


def test_duplicate_transactions_persist_distinct_tids(store):
    record = _make(store, "dup", [["a", "b"], ["a", "b"], ["a"]])
    assert record.transaction_count == 3

    db = store.load_vertical_database(record.corpus_id)
    assert db.items == ("a", "b")
    # T(a) = {1,2,3}, T(b) = {1,2}: duplicate rows keep tids 1 and 2.
    assert dict(zip(db.items, db.tidsets)) == {"a": (1, 2, 3), "b": (1, 2)}
    assert db.transaction_count == 3  # total includes all three


def test_intra_transaction_duplicates_leave_one_membership_row(store):
    record = _make(store, "dedup", [["a", "a", "a", "b"]])
    rows = store._conn.execute(
        "SELECT tid, item FROM transaction_items WHERE corpus_id = ? ORDER BY tid, item",
        (record.corpus_id,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [(1, "a"), (1, "b")]
    assert record.duplicate_item_occurrences == 2


def test_empty_transactions_stored_without_memberships(store):
    record = _make(store, "empties", [[], ["x"], []])
    assert record.empty_transaction_count == 2
    tx_rows = store._conn.execute(
        "SELECT tid, item_count FROM transactions WHERE corpus_id = ? ORDER BY tid",
        (record.corpus_id,
    )).fetchall()
    assert [tuple(r) for r in tx_rows] == [(1, 0), (2, 1), (3, 0)]
    assert store.item_domain(record.corpus_id) == ("x",)


def test_duplicate_corpus_name_rejected(store):
    _make(store, "same", [["a"]])
    with pytest.raises(DomainError) as exc:
        _make(store, "same", [["b"]])
    assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_independent_sql_index_matches_oracle(store):
    transactions = [["a", "b"], ["a", "b", "c"], ["c"], ["a", "c"]]
    record = _make(store, "idx", transactions)
    cid = record.corpus_id
    frequent, _, _ = mine_reference(transactions, 2)

    for itemset in frequent:
        if not itemset:
            continue
        tids = store.tidset_for_itemset(cid, tuple(sorted(itemset)))
        assert len(tids) == frequent[itemset]
        # Tids are sorted and distinct.
        assert tids == tuple(sorted(set(tids)))


def test_tidset_index_rejects_unknown_item(store):
    record = _make(store, "u", [["a"]])
    with pytest.raises(DomainError) as exc:
        store.tidset_for_itemset(record.corpus_id, ("a", "zzz"))
    assert exc.value.code is ErrorCode.ITEM_NOT_IN_DOMAIN
    assert exc.value.details["unknown_items"] == ["zzz"]


def test_closure_from_sql_index(store):
    transactions = [["a", "b", "c"], ["a", "b", "c"], ["c"]]
    record = _make(store, "cl", transactions)
    # T(a)=T(b)={1,2}, T(c)={1,2,3}. Closure only requires the extra item
    # to be present in EVERY supporting transaction; c also appearing alone
    # does not exclude it, so cl({a}) = cl({a,b}) = {a,b,c}.
    assert store.closure_of_itemset(record.corpus_id, ("a",)) == ("a", "b", "c")
    assert store.closure_of_itemset(record.corpus_id, ("a", "b")) == ("a", "b", "c")
    # Nothing else appears in all three c-transactions.
    assert store.closure_of_itemset(record.corpus_id, ("c",)) == ("c",)


def test_missing_corpus_and_job_categories(store):
    with pytest.raises(DomainError) as exc:
        store.get_corpus("nope")
    assert exc.value.code is ErrorCode.CORPUS_NOT_FOUND
    with pytest.raises(DomainError) as exc:
        store.get_job("nope")
    assert exc.value.code is ErrorCode.JOB_NOT_FOUND


def test_job_state_roundtrip_through_sqlite(store):
    transactions = [["a", "b"], ["a", "b", "c"], ["a", "c"], ["b"]]
    record = _make(store, "job", transactions)
    db = store.load_vertical_database(record.corpus_id)
    state = initial_state(db, 2)
    advance(state, 2)
    assert not state.completed
    job_id = store.create_job(record.corpus_id, 2, state)

    _, restored = store.state_snapshot(job_id)
    advance(restored, 10_000)
    store.save_job_state(job_id, restored)

    job, final = store.state_snapshot(job_id)
    assert job.status == "COMPLETED"
    _, closed_ref, maximal_ref = mine_reference(transactions, 2)
    assert {frozenset(r.itemset): r.support for r in final.results} == dict(closed_ref)
    assert {
        frozenset(r.itemset) for r in final.results if r.maximal
    } == set(maximal_ref)


def test_kernel_version_mismatch_detected(store):
    record = _make(store, "ver", [["a"]])
    db = store.load_vertical_database(record.corpus_id)
    job_id = store.create_job(record.corpus_id, 1, initial_state(db, 1))
    store._conn.execute(
        "UPDATE jobs SET kernel_version = '0.0.1-bogus' WHERE job_id = ?",
        (job_id,),
    )
    job = store.get_job(job_id)
    with pytest.raises(DomainError) as exc:
        store.load_mining_state(job)
    assert exc.value.code is ErrorCode.STATE_VERSION_MISMATCH
