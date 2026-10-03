"""HTTP API contract tests: success paths plus the four error categories."""

from __future__ import annotations

import json
import logging

import numpy as np

logger = logging.getLogger("opp506.tests")


def _create_channel(client, **overrides) -> str:
    payload = {"algorithm": "nlms", "filter_len": 4, "mu": 0.5}
    payload.update(overrides)
    response = client.post("/channels", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["channel_id"]


def test_health(client) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_process_block_roundtrip(client) -> None:
    channel_id = _create_channel(client)
    rng = np.random.default_rng(0)
    ref = rng.standard_normal(64).tolist()
    pri = rng.standard_normal(64).tolist()

    response = client.post(
        f"/channels/{channel_id}/process",
        json={"reference": ref, "primary": pri, "freeze_intervals": [{"start": 10, "end": 20}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"]
    assert body["samples"] == 64
    assert body["adapted_samples"] == 54
    assert body["frozen_samples"] == 10
    assert len(body["output"]) == len(body["error"]) == 64
    assert body["min_denominator"] > 0.0

    state = client.get(f"/channels/{channel_id}/state").json()
    assert state["samples_processed"] == 64
    assert len(state["weights"]) == 4
    logger.info(
        "api run replay: run_id=%s weight_norm_after=%.6f",
        body["run_id"], body["weight_norm_after"],
    )


def test_streaming_state_accumulates_across_requests(client) -> None:
    channel_id = _create_channel(client)
    rng = np.random.default_rng(1)
    for _ in range(3):
        client.post(
            f"/channels/{channel_id}/process",
            json={"reference": rng.standard_normal(32).tolist(),
                  "primary": rng.standard_normal(32).tolist()},
        ).raise_for_status()
    state = client.get(f"/channels/{channel_id}/state").json()
    assert state["samples_processed"] == 96


def test_unknown_channel_is_state_conflict(client) -> None:
    response = client.post(
        "/channels/nope/process", json={"reference": [1.0], "primary": [1.0]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["category"] == "state_conflict"


def test_deleted_channel_is_state_conflict(client) -> None:
    channel_id = _create_channel(client)
    assert client.delete(f"/channels/{channel_id}").status_code == 204
    response = client.get(f"/channels/{channel_id}/state")
    assert response.status_code == 409
    assert response.json()["error"]["category"] == "state_conflict"


def test_out_of_range_learning_rate_is_input_error(client) -> None:
    for bad_mu in (0.0, 2.0, 3.5, -0.1):
        response = client.post(
            "/channels", json={"algorithm": "nlms", "filter_len": 4, "mu": bad_mu}
        )
        assert response.status_code == 400, bad_mu
        assert response.json()["error"]["category"] == "input_error"


def test_nan_sample_is_input_error(client) -> None:
    channel_id = _create_channel(client)
    # JSON has no NaN literal; send it the way Python clients do.
    payload = {"reference": [1.0, float("nan")], "primary": [0.0, 0.0]}
    response = client.post(
        f"/channels/{channel_id}/process",
        content=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "input_error"


def test_mismatched_block_lengths_is_input_error(client) -> None:
    channel_id = _create_channel(client)
    response = client.post(
        f"/channels/{channel_id}/process",
        json={"reference": [1.0, 2.0], "primary": [1.0]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "input_error"


def test_bad_freeze_interval_is_input_error(client) -> None:
    channel_id = _create_channel(client)
    response = client.post(
        f"/channels/{channel_id}/process",
        json={
            "reference": [0.0] * 8,
            "primary": [0.0] * 8,
            "freeze_intervals": [{"start": 4, "end": 99}],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "input_error"


def test_evaluate_correlated_noise_succeeds(client) -> None:
    response = client.post(
        "/evaluate",
        json={"scenario": "correlated_noise", "n_samples": 8000, "seed": 42, "mu": 0.02},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    logger.info(
        "evaluate run replay: run_id=%s snr_improvement_db=%.2f coeff_err=%.5f",
        body["run_id"], body["snr_improvement_db"], body["coefficient_error_norm"],
    )
    assert body["snr_improvement_db"] > 15.0
    # Instantaneous final coefficient error fluctuates around a stationary
    # floor (~0.07 mean across seeds); 0.15 bounds the observed spread.
    assert body["coefficient_error_norm"] < 0.15
    assert len(body["final_weights"]) == len(body["true_coeffs"]) == 4


def test_evaluate_decorrelated_reference_reports_failure(client) -> None:
    response = client.post(
        "/evaluate",
        json={"scenario": "decorrelated_reference", "n_samples": 4000, "seed": 42},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert abs(body["snr_improvement_db"]) < 3.0


def test_evaluate_unknown_scenario_is_input_error(client) -> None:
    response = client.post("/evaluate", json={"scenario": "real_recording"})
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "input_error"
