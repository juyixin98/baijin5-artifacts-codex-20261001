"""Evidence & diagnostics: independent replay audit and FDP bookkeeping.

Tampering tests mutate SQLite directly (simulating post-hoc rewriting of a
p-value) and assert the audit names the specific mismatch rather than merely
failing opaquely.
"""

import pytest

from app.contracts import Decision, W0, replay
from app.diagnostics import discovery_stats, replay_verify
from app.errors import EvidenceTamperedError
from app.store import SQLiteStore, row_hash


def _build(store, ps):
    run = store.create_run()
    for i, p in enumerate(ps, start=1):
        store.append_decision(run.run_id, f"H{i}", p)
    return run.run_id


def test_clean_run_passes_full_replay_audit():
    store = SQLiteStore(":memory:")
    rid = _build(store, [1e-7, 0.1, 0.00025, 0.07, 0.5, 1e-6])
    report = replay_verify(store, rid, raise_on_mismatch=False)
    assert report.ok is True
    assert report.chain_ok is True
    assert report.run_counters_ok is True
    assert report.contract_fingerprint_ok is True
    assert report.mismatches == ()
    assert report.n_decisions == 6


def test_empty_run_audit_is_consistent():
    store = SQLiteStore(":memory:")
    run = store.create_run()
    report = replay_verify(store, run.run_id, raise_on_mismatch=False)
    assert report.ok is True
    assert report.n_decisions == 0
    assert report.run_counters_ok is True


def test_retroactive_pvalue_rewrite_is_detected_by_replay():
    store = SQLiteStore(":memory:")
    rid = _build(store, [0.4, 0.5, 0.6])
    # Attacker tries to turn the non-rejected first p-value into a discovery.
    with store._conn:  # noqa: SLF001 - deliberate tamper simulation
        store._conn.execute(
            "UPDATE decisions SET p_value=1e-12 WHERE run_id=? AND idx=1",
            (rid,),
        )
    report = replay_verify(store, rid, raise_on_mismatch=False)
    assert report.ok is False
    fields = {m.field_name for m in report.mismatches}
    # Replay from stored p-values now disagrees about rejection AND the
    # downstream anchor/threshold chain.
    assert "rejected" in fields or "threshold" in fields or "tau" in fields


def test_retroactive_pvalue_rewrite_also_breaks_hash_chain():
    store = SQLiteStore(":memory:")
    rid = _build(store, [0.4, 0.5])
    with store._conn:  # noqa: SLF001
        store._conn.execute(
            "UPDATE decisions SET p_value=0.0 WHERE run_id=? AND idx=1",
            (rid,),
        )
    report = replay_verify(store, rid, raise_on_mismatch=False)
    assert report.chain_ok is False
    assert any(m.field_name == "row_hash" for m in report.mismatches)
    with pytest.raises(EvidenceTamperedError):
        replay_verify(store, rid)  # default raises


def test_threshold_rewrite_without_pvalue_change_is_detected():
    store = SQLiteStore(":memory:")
    rid = _build(store, [0.4, 0.5])
    with store._conn:  # noqa: SLF001
        store._conn.execute(
            "UPDATE decisions SET threshold=0.9 WHERE run_id=? AND idx=1",
            (rid,),
        )
    report = replay_verify(store, rid, raise_on_mismatch=False)
    assert report.ok is False
    assert any(m.field_name == "threshold" for m in report.mismatches)
    assert report.chain_ok is False


def test_wealth_counter_drift_is_detected():
    store = SQLiteStore(":memory:")
    rid = _build(store, [1e-7, 0.5])
    with store._conn:  # noqa: SLF001
        store._conn.execute(
            "UPDATE runs SET wealth=0.5 WHERE run_id=?", (rid,)
        )
    report = replay_verify(store, rid, raise_on_mismatch=False)
    assert report.run_counters_ok is False
    assert report.ok is False


def test_row_hash_is_contract_sensitive():
    d = Decision(
        index=1, hypothesis_id="H1", p_value=0.5, threshold=0.0001,
        gamma_value=0.01, rejected=False, wealth_before=W0,
        wealth_after=W0 - 0.0001, tau=0, w_tau_used=W0, reason="x",
    )
    h1 = row_hash("r", d, "0" * 64, "fingerprint-A")
    h2 = row_hash("r", d, "0" * 64, "fingerprint-B")
    h3 = row_hash("r", Decision(
        index=1, hypothesis_id="H1", p_value=0.5000001, threshold=0.0001,
        gamma_value=0.01, rejected=False, wealth_before=W0,
        wealth_after=W0 - 0.0001, tau=0, w_tau_used=W0, reason="x",
    ), "0" * 64, "fingerprint-A")
    assert h1 != h2 and h1 != h3 and len(h1) == 64


# --- False discovery accounting against ground-truth labels ----------------

def test_discovery_stats_all_null_no_rejections():
    decisions = replay([(f"H{i}", 0.5) for i in range(1, 11)])
    stats = discovery_stats(decisions, [True] * 10)
    assert stats.n_rejections == 0
    assert stats.false_discoveries == 0
    assert stats.fdp == 0.0
    assert stats.power == 0.0


def test_discovery_stats_mixed_labels_count_v_and_power():
    # Hand-built: two rejections, one on a null, one on a non-null.
    ps = [1e-9, 1e-9, 0.9]
    decisions = replay([(f"H{i}", p) for i, p in enumerate(ps, 1)])
    assert [d.rejected for d in decisions] == [True, True, False]
    labels = [True, False, True]  # first rejection is a false discovery
    stats = discovery_stats(decisions, labels)
    assert stats.n_rejections == 2
    assert stats.false_discoveries == 1
    assert stats.fdp == 0.5
    assert stats.power == 1.0  # the single non-null was rejected
    payload = stats.to_dict()
    assert payload["false_discoveries"] == 1


def test_discovery_stats_requires_one_label_per_decision():
    decisions = replay([("H1", 0.5)])
    with pytest.raises(ValueError):
        discovery_stats(decisions, [])
