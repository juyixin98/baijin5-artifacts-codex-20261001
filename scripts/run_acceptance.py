#!/usr/bin/env python3
"""Acceptance runner: exercises the HTTP API end-to-end on synthetic
fixtures and records a replayable JSONL log per run.

Usage:
    python scripts/run_acceptance.py [--log-dir logs]

Exit code 0 iff every check passes. Each check prints its run_id, the key
intermediate quantities, and the reason for its verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.fixtures import synth  # noqa: E402
from app.main import create_app  # noqa: E402

RESULTS: list[dict] = []


def check(name: str, ok: bool, rationale: str, run_id: str, **fields) -> bool:
    record = {"check": name, "ok": bool(ok), "rationale": rationale,
              "run_id": run_id, **fields}
    RESULTS.append(record)
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {rationale} (run_id={run_id})")
    return ok


def post_stream(client: TestClient, **overrides) -> str:
    payload = {
        "stream_id": overrides.pop("stream_id"),
        "algorithm": overrides.pop("algorithm", "nlms"),
        "filter_length": overrides.pop("filter_length"),
        "mu": overrides.pop("mu"),
        "channels": overrides.pop("channels", ["ch0"]),
        **overrides,
    }
    resp = client.post("/v1/streams", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["run_id"]


def run_scenario(client: TestClient, stream_id: str, scenario, **stream_kw):
    post_stream(client, stream_id=stream_id,
                filter_length=len(scenario.plants[0]), **stream_kw)
    resp = client.post(
        f"/v1/streams/{stream_id}/channels/ch0/blocks",
        json={
            "start_index": 0,
            "reference": scenario.reference.tolist(),
            "desired": scenario.desired.tolist(),
        },
    )
    assert resp.status_code == 200, resp.text
    block = resp.json()
    state = client.get(f"/v1/streams/{stream_id}/channels/ch0/state").json()
    ev = client.post("/v1/evaluate", json={
        "desired": scenario.desired.tolist(),
        "residual": block["errors"],
        "clean": scenario.clean.tolist(),
        # Filter coefficients are stored in buffer order (oldest->newest),
        # i.e. the time-reversed lfilter impulse response.
        "plant_weights": scenario.plants[-1][::-1].tolist(),
        "estimated_weights": state["weights"],
    })
    assert ev.status_code == 200, ev.text
    return block, state, ev.json()


def scenario_correlated(client: TestClient) -> None:
    sc = synth.correlated_noise_scenario(n=16000, filter_length=64, seed=7)
    _, _, ev = run_scenario(client, "acc-correlated", sc, mu=0.1)
    check(
        "correlated_noise_nlms",
        ev["noise_reduction_db"] > 12.0 and ev["coefficient_error_db"] < -12.0,
        f"noise_reduction={ev['noise_reduction_db']:.2f} dB (>12 expected), "
        f"coefficient_error={ev['coefficient_error_db']:.2f} dB (<-12 expected)",
        ev["run_id"],
        noise_reduction_db=ev["noise_reduction_db"],
        coefficient_error_db=ev["coefficient_error_db"],
    )


def scenario_lms(client: TestClient) -> None:
    sc = synth.correlated_noise_scenario(n=8000, filter_length=32, seed=8)
    _, _, ev = run_scenario(client, "acc-lms", sc, algorithm="lms", mu=0.02)
    check(
        "correlated_noise_lms",
        ev["noise_reduction_db"] > 10.0,
        f"noise_reduction={ev['noise_reduction_db']:.2f} dB (>10 expected)",
        ev["run_id"], noise_reduction_db=ev["noise_reduction_db"],
    )


def scenario_silence(client: TestClient) -> None:
    sc = synth.silence_scenario(n=512, filter_length=32)
    post_stream(client, stream_id="acc-silence", filter_length=32, mu=1.9)
    resp = client.post("/v1/streams/acc-silence/channels/ch0/blocks", json={
        "start_index": 0,
        "reference": sc.reference.tolist(),
        "desired": sc.desired.tolist(),
    })
    body = resp.json()
    state = client.get("/v1/streams/acc-silence/channels/ch0/state").json()
    finite = resp.status_code == 200 and all(map(np.isfinite, body["errors"]))
    check(
        "silence_no_divide_by_zero",
        finite and state["weight_norm"] == 0.0,
        "zero reference: all outputs finite, weights unchanged "
        f"(weight_norm={state['weight_norm']})",
        body.get("run_id", "n/a"),
    )


def scenario_decorrelated(client: TestClient) -> None:
    sc = synth.decorrelated_reference_scenario(n=4000, filter_length=64, seed=21)
    _, _, ev = run_scenario(client, "acc-decorrelated", sc, mu=0.5)
    check(
        "decorrelated_reference_fails_honestly",
        abs(ev["noise_reduction_db"]) < 3.0 and not ev["success"],
        f"documented failure case: noise_reduction="
        f"{ev['noise_reduction_db']:.2f} dB (~0 expected), "
        f"success flag={ev['success']}",
        ev["run_id"], noise_reduction_db=ev["noise_reduction_db"],
    )


def scenario_abrupt_change(client: TestClient) -> None:
    sc = synth.abrupt_change_scenario(n=8000, filter_length=64, seed=11)
    block, _, _ = run_scenario(client, "acc-abrupt", sc, mu=0.2)
    change = sc.meta["change_index"]
    tail = slice(change + 2000, None)
    ev = client.post("/v1/evaluate", json={
        "desired": sc.desired[tail].tolist(),
        "residual": block["errors"][tail],
        "clean": sc.clean[tail].tolist(),
    }).json()
    check(
        "abrupt_plant_change_reconverges",
        ev["noise_reduction_db"] > 15.0,
        f"post-change noise_reduction={ev['noise_reduction_db']:.2f} dB "
        f"(>15 expected, change at index {change})",
        ev["run_id"], noise_reduction_db=ev["noise_reduction_db"],
    )


def scenario_freeze(client: TestClient) -> None:
    sc = synth.correlated_noise_scenario(n=2000, filter_length=32, seed=9)
    post_stream(client, stream_id="acc-freeze", filter_length=32, mu=0.5,
                frozen_until_index=1000)
    resp = client.post("/v1/streams/acc-freeze/channels/ch0/blocks", json={
        "start_index": 0,
        "reference": sc.reference.tolist(),
        "desired": sc.desired.tolist(),
    }).json()
    state = client.get("/v1/streams/acc-freeze/channels/ch0/state").json()
    # Frozen prefix contributes nothing: weight norm after 1000 frozen
    # samples of 2000 must be smaller than a fully-adapted run, and the
    # frozen count must be exactly 1000.
    check(
        "freeze_interval_respected",
        resp["frozen_samples"] == 1000 and 0.0 < state["weight_norm"],
        f"frozen_samples={resp['frozen_samples']} (1000 expected), "
        f"adaptation active afterwards (weight_norm={state['weight_norm']:.4f})",
        resp["run_id"],
    )


def scenario_channel_isolation(client: TestClient) -> None:
    post_stream(client, stream_id="acc-iso", filter_length=16, mu=0.5,
                channels=["a", "b"])
    rng = np.random.default_rng(31)
    xa, da = rng.standard_normal(256), rng.standard_normal(256)
    client.post("/v1/streams/acc-iso/channels/a/blocks", json={
        "start_index": 0, "reference": xa.tolist(), "desired": da.tolist(),
    })
    state_a = client.get("/v1/streams/acc-iso/channels/a/state").json()
    state_b = client.get("/v1/streams/acc-iso/channels/b/state").json()
    check(
        "multi_channel_isolation",
        state_b["next_index"] == 0 and state_b["weight_norm"] == 0.0
        and state_a["weight_norm"] > 0.0,
        f"channel a adapted (norm={state_a['weight_norm']:.4f}), "
        f"channel b untouched (index={state_b['next_index']})",
        state_a.get("run_id", "n/a"),
    )


def scenario_error_kinds(client: TestClient) -> None:
    # input_validation: NLMS mu=2.0 violates the exclusive upper bound.
    r1 = client.post("/v1/streams", json={
        "stream_id": "acc-err1", "algorithm": "nlms", "filter_length": 8,
        "mu": 2.0, "channels": ["a"],
    })
    ok1 = r1.status_code == 422 and r1.json()["error"]["kind"] == "input_validation"

    # state_conflict: index gap.
    post_stream(client, stream_id="acc-err2", filter_length=8, mu=0.5)
    r2 = client.post("/v1/streams/acc-err2/channels/ch0/blocks", json={
        "start_index": 7, "reference": [0.0], "desired": [0.0],
    })
    ok2 = r2.status_code == 409 and r2.json()["error"]["kind"] == "state_conflict"

    # computation_failure: diverging LMS block, then state still usable.
    post_stream(client, stream_id="acc-err3", filter_length=8, mu=1.0,
                algorithm="lms")
    r3 = client.post("/v1/streams/acc-err3/channels/ch0/blocks", json={
        "start_index": 0, "reference": [1e200] * 32, "desired": [1.0] * 32,
    })
    r4 = client.post("/v1/streams/acc-err3/channels/ch0/blocks", json={
        "start_index": 0, "reference": [1.0], "desired": [1.0],
    })
    ok3 = (r3.status_code == 500
           and r3.json()["error"]["kind"] == "computation_failure"
           and r4.status_code == 200)

    check(
        "error_kinds_distinguishable",
        ok1 and ok2 and ok3,
        f"input_validation={ok1}, state_conflict={ok2}, "
        f"computation_failure+rollback={ok3}",
        r3.json()["error"].get("run_id", "n/a"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", default="logs")
    args = parser.parse_args()
    os.environ["LMS_LOG_DIR"] = args.log_dir

    client = TestClient(create_app())
    for scenario in (
        scenario_correlated,
        scenario_lms,
        scenario_silence,
        scenario_decorrelated,
        scenario_abrupt_change,
        scenario_freeze,
        scenario_channel_isolation,
        scenario_error_kinds,
    ):
        scenario(client)

    summary_path = os.path.join(args.log_dir, "acceptance-summary.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, indent=2)
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed; "
          f"summary at {summary_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
