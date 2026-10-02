"""Job-runner tests: batch isolation and tiled aggregation."""
import numpy as np

from app.config import DEFAULT_CONFIG
from app.jobs import Job, estimate_tiled, run_batch


def test_batch_isolates_failures(pair):
    ref_ok, mov_ok, _ = pair("integer_shift")
    ref_flat, mov_flat, _ = pair("constant_image")
    jobs = [
        Job(ref=ref_ok, mov=mov_ok, job_id="ok-job", config=DEFAULT_CONFIG),
        Job(ref=ref_flat, mov=mov_flat, job_id="flat-job", config=DEFAULT_CONFIG),
        Job(ref=np.zeros((16, 16)), mov=np.zeros((16, 32)), job_id="bad-job",
            config=DEFAULT_CONFIG),
    ]
    results = run_batch(jobs)
    assert [r.job_id for r in results] == ["ok-job", "flat-job", "bad-job"]
    assert results[0].ok and results[0].result["status"] == "ok"
    assert not results[1].ok and results[1].result["failure_reason"] == "flat_response"
    assert not results[2].ok and results[2].result["failure_reason"] == "shape_mismatch"
    # one failing job did not abort the batch
    assert all(r.duration_ms >= 0 for r in results)


def test_tiled_estimation_recovers_shift(pair):
    ref, mov, entry = pair("integer_shift")
    out = estimate_tiled(ref, mov, tile_size=64, stride=32, config=DEFAULT_CONFIG)
    assert out["status"] == "ok"
    assert out["n_usable_tiles"] >= 2
    gt = entry["ground_truth_shift"]
    assert abs(out["shift"]["dy"] - gt[0]) < 0.5
    assert abs(out["shift"]["dx"] - gt[1]) < 0.5
    assert out["tile_spread_px"] < 0.5


def test_tiled_estimation_reports_tile_failures(pair):
    ref, mov, _ = pair("constant_image")
    out = estimate_tiled(ref, mov, tile_size=64, stride=32, config=DEFAULT_CONFIG)
    assert out["status"] == "failed"
    assert out["failure_reason"] == "no_usable_tiles"
