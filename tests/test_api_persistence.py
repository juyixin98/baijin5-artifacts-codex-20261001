"""API + persistence tests: causal protocol, restart, identity, tampering.

These tests assert concrete outcomes and exact failure categories, never just
"endpoint callable".
"""

from __future__ import annotations

import math

import pytest

from app.errors import ErrorCode
from app.statistics import LordConfig
from app.storage import GENESIS, RunStore

HAND_PS = [0.0005, 0.5, 0.0005, 0.9, 0.0004]
EXPECTED_THRESHOLDS = [
    0.0006461705090967012,
    0.000647068228589669,
    0.0006837192518151271,
    0.0006480090542068497,
    0.0006018049450165025,
]


def _drive(store: RunStore, run_id: str, p_values, ids=None):
    ids = ids or [f"h{i}" for i in range(1, len(p_values) + 1)]
    out = []
    for hid, p in zip(ids, p_values):
        store.reserve(run_id, hid)
        out.append(store.decide(run_id, hid, p))
    return out


# ------------------------------------------------------------- happy path --


def test_full_hand_trajectory_over_http(client):
    r = client.post("/runs", json={"run_id": "run-hand", "alpha": 0.05, "w0": 0.045})
    assert r.status_code == 201
    assert r.json()["data"]["contract_version"] == "lordpp-v1"

    for i, (p, expected) in enumerate(zip(HAND_PS, EXPECTED_THRESHOLDS), start=1):
        hid = f"hyp-{i}"
        r = client.post("/runs/run-hand/reserve", json={"hypothesis_id": hid})
        assert r.status_code == 201
        slot = r.json()["data"]
        assert slot["status"] == "pending"
        assert slot["p_value"] is None
        assert math.isclose(slot["threshold"], expected, rel_tol=1e-12, abs_tol=1e-15)
        threshold_before_p = slot["threshold"]

        r = client.post(
            "/runs/run-hand/decide", json={"hypothesis_id": hid, "p_value": p}
        )
        assert r.status_code == 200
        decided = r.json()["data"]
        assert decided["status"] == "decided"
        # The committed threshold is bit-stable: observing p did not rewrite it.
        assert decided["threshold"] == threshold_before_p
        assert decided["rejected"] is (p <= expected)

    r = client.get("/runs/run-hand/replay")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["decisions_checked"] == 5
    assert data["rejections"] == 3
    assert [s["idx"] for s in data["steps"] if s["rejected"]] == [1, 3, 5]
    assert data["head_hash"] != GENESIS


# --------------------------------------------------- causality / protocol --


def test_cannot_decide_without_reservation(client):
    client.post("/runs", json={"run_id": "r"})
    r = client.post("/runs/r/decide", json={"hypothesis_id": "h1", "p_value": 0.01})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == ErrorCode.STATE_CONFLICT


def test_cannot_have_two_pending_slots(client):
    client.post("/runs", json={"run_id": "r"})
    client.post("/runs/r/reserve", json={"hypothesis_id": "h1"})
    r = client.post("/runs/r/reserve", json={"hypothesis_id": "h2"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == ErrorCode.STATE_CONFLICT


def test_post_hoc_pvalue_change_is_blocked_and_keeps_budget(client):
    client.post("/runs", json={"run_id": "r", "alpha": 0.05, "w0": 0.045})
    client.post("/runs/r/reserve", json={"hypothesis_id": "h1"})
    first = client.post(
        "/runs/r/decide", json={"hypothesis_id": "h1", "p_value": 0.0005}
    ).json()["data"]
    assert first["rejected"] is True
    frozen_threshold = first["threshold"]

    # Attempt to rewrite history with a different (even invalid) p-value.
    r = client.post(
        "/runs/r/decide", json={"hypothesis_id": "h1", "p_value": 0.9}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == ErrorCode.STATE_CONFLICT
    r = client.post("/runs/r/decide", json={"hypothesis_id": "h1", "p_value": 0.0})
    assert r.status_code == 409

    # The original record and the spent budget are untouched.
    steps = client.get("/runs/r/steps").json()["data"]
    assert steps[0]["p_value"] == 0.0005
    assert steps[0]["threshold"] == frozen_threshold
    assert steps[0]["rejected"] is True
    # A later threshold already reflects h1's rejection reward (history fixed).
    client.post("/runs/r/reserve", json={"hypothesis_id": "h2"})
    t2 = client.post(
        "/runs/r/decide", json={"hypothesis_id": "h2", "p_value": 0.5}
    ).json()["data"]["threshold"]
    assert math.isclose(t2, EXPECTED_THRESHOLDS[1], rel_tol=1e-12)


def test_duplicate_hypothesis_identity_is_state_conflict(client):
    client.post("/runs", json={"run_id": "r"})
    client.post("/runs/r/reserve", json={"hypothesis_id": "dup"})
    client.post("/runs/r/decide", json={"hypothesis_id": "dup", "p_value": 0.5})
    r = client.post("/runs/r/reserve", json={"hypothesis_id": "dup"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == ErrorCode.STATE_CONFLICT


def test_same_identity_allowed_in_a_different_run(client):
    client.post("/runs", json={"run_id": "a"})
    client.post("/runs", json={"run_id": "b"})
    r1 = client.post("/runs/a/reserve", json={"hypothesis_id": "gene-7"})
    r2 = client.post("/runs/b/reserve", json={"hypothesis_id": "gene-7"})
    assert r1.status_code == 201
    assert r2.status_code == 201


# ------------------------------------------------------- error taxonomy ----


def test_input_error_categories_distinguished(client):
    client.post("/runs", json={"run_id": "r"})
    # Framework-level shape validation is normalized into the same envelope:
    # illegal characters in hypothesis_id fail the schema validator.
    r = client.post("/runs/r/reserve", json={"hypothesis_id": "bad id!"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == ErrorCode.INPUT_ERROR

    # Bad p-value through a valid reservation -> 400 INPUT_ERROR.
    # NaN/inf are not representable in strict JSON (rejected at transport
    # boundary); their rejection by the kernel is covered at unit level.
    client.post("/runs/r/reserve", json={"hypothesis_id": "h1"})
    for bad in (0.0, 1.5, -0.2, "x"):
        r = client.post(
            "/runs/r/decide", json={"hypothesis_id": "h1", "p_value": bad}
        )
        assert r.status_code == 400, (bad, r.status_code)
        assert r.json()["error"]["code"] == ErrorCode.INPUT_ERROR, bad
    # State must still be pending and recoverable after rejected inputs.
    r = client.post(
        "/runs/r/decide", json={"hypothesis_id": "h1", "p_value": 0.5}
    )
    assert r.status_code == 200


def test_resource_exhaustion_is_its_own_category(client):
    client.post("/runs", json={"run_id": "small", "horizon": 2})
    for hid, p in [("a", 0.5), ("b", 0.5)]:
        client.post(f"/runs/small/reserve", json={"hypothesis_id": hid})
        client.post(f"/runs/small/decide", json={"hypothesis_id": hid, "p_value": p})
    r = client.post("/runs/small/reserve", json={"hypothesis_id": "c"})
    assert r.status_code == 413
    body = r.json()
    assert body["error"]["code"] == ErrorCode.RESOURCE_EXHAUSTED
    assert body["error"]["details"]["horizon"] == 2
    # Completed run is marked.
    assert client.get("/runs/small").json()["data"]["status"] == "completed"


def test_unknown_run_is_not_found(client):
    r = client.get("/runs/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND


def test_invalid_run_creation_inputs(client):
    r = client.post("/runs", json={"alpha": 0.0})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == ErrorCode.INPUT_ERROR
    r = client.post("/runs", json={"alpha": 0.05, "w0": 0.5})
    assert r.status_code == 400
    r = client.post("/runs", json={"horizon": 100001})
    assert r.status_code == 400
    # Extra/unknown fields are rejected and normalized to INPUT_ERROR.
    r = client.post("/runs", json={"unknown_field": 1})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == ErrorCode.INPUT_ERROR
    # Wrong field type is framework-validated to the same envelope.
    r = client.post("/runs", json={"alpha": "not-a-number"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == ErrorCode.INPUT_ERROR


# --------------------------------------------------------------- restart ---


def test_run_state_survives_store_restart(tmp_path):
    db = tmp_path / "fdr.db"
    s1 = RunStore(str(db))
    s1.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="restart")
    s1.reserve("restart", "h1")
    s1.decide("restart", "h1", 0.0005)
    s1.reserve("restart", "h2")
    s1.decide("restart", "h2", 0.5)
    head_after_two = s1.get_run("restart")["head_hash"]
    s1.close()

    # Reopen: committed decisions persist; the kernel rebuilds from history.
    s2 = RunStore(str(db))
    report = s2.replay("restart")
    assert report["decisions_checked"] == 2
    assert report["head_hash"] == head_after_two
    # Continue from slot 3 with identical threshold trajectory.
    row = s2.reserve("restart", "h3")
    assert math.isclose(row["threshold"], EXPECTED_THRESHOLDS[2], rel_tol=1e-12)
    s2.close()


def test_restart_after_pending_slot_does_not_lose_or_duplicate(tmp_path):
    db = tmp_path / "pend.db"
    s1 = RunStore(str(db))
    s1.create_run(LordConfig.create(), run_id="p")
    s1.reserve("p", "only")
    s1.close()  # crash while pending

    s2 = RunStore(str(db))
    # The pending slot still owns the next index; a new identity is blocked
    # until the pending hypothesis is decided.
    with pytest.raises(Exception) as ei:
        s2.reserve("p", "other")
    assert ei.value.code == ErrorCode.STATE_CONFLICT
    row = s2.decide("p", "only", 0.5)
    assert row["idx"] == 1
    s2.close()


# -------------------------------------------------------------- tampering --


def test_direct_pvalue_tampering_is_detected_by_replay(store: RunStore):
    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="tamper")
    _drive(store, "tamper", HAND_PS)
    honest_head = store.get_run("tamper")["head_hash"]

    # Adversary edits a historical p-value directly in the database.
    with store._conn:  # noqa: SLF001 - deliberate tamper in the test fixture
        store._conn.execute(
            "UPDATE steps SET p_value=0.9 WHERE run_id=? AND idx=1", ("tamper",)
        )
    with pytest.raises(Exception) as ei:
        store.replay("tamper")
    assert ei.value.code == ErrorCode.INTEGRITY_ERROR
    # The run-level head hash is stale relative to the tampered row set.
    assert store.get_run("tamper")["head_hash"] == honest_head


def test_threshold_tampering_is_detected(store: RunStore):
    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="tt")
    _drive(store, "tt", HAND_PS)
    with store._conn:  # noqa: SLF001
        store._conn.execute(
            "UPDATE steps SET threshold=threshold*10 WHERE run_id=? AND idx=3",
            ("tt",),
        )
    with pytest.raises(Exception) as ei:
        store.replay("tt")
    assert ei.value.code == ErrorCode.INTEGRITY_ERROR


def test_row_deletion_breaks_chain(store: RunStore):
    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="del")
    _drive(store, "del", HAND_PS)
    with store._conn:  # noqa: SLF001
        store._conn.execute("DELETE FROM steps WHERE run_id=? AND idx=2", ("del",))
    with pytest.raises(Exception) as ei:
        store.replay("del")
    assert ei.value.code == ErrorCode.INTEGRITY_ERROR


# ----------------------------------------------------------------- events --


def test_event_log_records_run_numbers_and_reasons(client):
    client.post("/runs", json={"run_id": "ev", "alpha": 0.05, "w0": 0.045})
    client.post("/runs/ev/reserve", json={"hypothesis_id": "h1"})
    client.post("/runs/ev/decide", json={"hypothesis_id": "h1", "p_value": 0.0005})
    # Trigger a classified error so its category is logged.
    client.post("/runs/ev/reserve", json={"hypothesis_id": "h1"})

    events = client.get("/runs/ev/events").json()["data"]
    kinds = {e["kind"] for e in events}
    assert {"info", "reserve", "decide", "reject", "error"} <= kinds
    err = next(e for e in events if e["kind"] == "error")
    assert err["code"] == ErrorCode.STATE_CONFLICT
    # Events are returned newest-first; sequence numbers must still be intact.
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs, reverse=True)
    assert set(seqs) == set(range(1, len(seqs) + 1))
    decision = next(e for e in events if e["kind"] == "decide")
    assert decision["payload"]["threshold"] == EXPECTED_THRESHOLDS[0]
    assert decision["payload"]["wealth_after"] > decision["payload"]["wealth_before"]


def test_contract_endpoint_freezes_parameters(client):
    data = client.get("/contract").json()["data"]
    assert data["contract_version"] == "lordpp-v1"
    assert data["alpha_default"] == 0.05
    assert data["w0_default"] == pytest.approx(0.045, abs=1e-12)
    assert data["payoff_default"] == pytest.approx(0.005, abs=1e-12)
    assert data["horizon_default"] == 1000
    assert data["schedule_constant_c"] == 0.0722
    assert "expectation" in data["not_guaranteed"][0].lower()
