"""Integration test over the committed sample fixtures in ``data/``.

The expected properties (seed labels, certificate invariants, energy
decomposition identity) are asserted from first principles; the only
quantity taken from the implementation is the labeling itself, which is
then re-checked by an independent energy evaluation in this file.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from graphcut.config import Settings
from graphcut.contracts import Seed, validate_pairwise
from graphcut.imaging import IntensityQuadraticModel, decode_png_base64
from graphcut.main import create_app

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


@pytest.fixture(scope="module")
def sample_request():
    path = DATA_DIR / "sample_request.json"
    if not path.exists():
        pytest.skip("sample data not generated; run scripts/make_sample_data.py")
    return json.loads(path.read_text(encoding="utf-8"))


def test_sample_request_segments_with_certificate(sample_request, test_log):
    with TestClient(create_app(Settings())) as client:
        resp = client.post("/v1/segment", json=sample_request)
    assert resp.status_code == 200
    body = resp.json()
    cert = body["certificate"]
    test_log.info("sample.run run_id=%s energy=%s flow=%s gaps=(%.3e, %.3e)",
                  body["run_id"], body["energy"]["total"],
                  cert["flow_value"], cert["abs_gap_flow_cut"],
                  cert["abs_gap_cut_energy"])
    assert cert["verified"] is True
    assert cert["seeds_satisfied"] is True
    assert cert["abs_gap_flow_cut"] <= cert["tolerance"]
    assert cert["abs_gap_cut_energy"] <= cert["tolerance"]
    # Energy decomposition identity.
    assert body["energy"]["data"] + body["energy"]["smooth"] == \
        pytest.approx(body["energy"]["total"])

    # Independent re-check: rebuild unaries from the PNG and recompute the
    # energy of the returned labeling in plain Python.
    intensity = decode_png_base64(sample_request["image"]["data"])
    model = IntensityQuadraticModel(
        fg_mean=sample_request["data_model"]["fg_mean"],
        bg_mean=sample_request["data_model"]["bg_mean"],
        sigma=sample_request["data_model"]["sigma"],
    )
    unary0, unary1 = model.unaries(intensity)
    labels = np.array(body["labels"])
    height, width = labels.shape
    w = sample_request["pairwise"]["weight"]
    energy = 0.0
    for r in range(height):
        for c in range(width):
            lab = int(labels[r, c])
            energy += float(unary1[r, c] if lab else unary0[r, c])
            if c + 1 < width and int(labels[r, c + 1]) != lab:
                energy += w
            if r + 1 < height and int(labels[r + 1, c]) != lab:
                energy += w
    assert energy == pytest.approx(body["energy"]["total"], rel=1e-9)

    # Hard seeds honored.
    for seed in sample_request["seeds"]:
        assert labels[seed["row"], seed["col"]] == seed["label"]

    # Sanity on the synthetic scene: the circle center is foreground,
    # corners are background, and the foreground is one connected blob of
    # plausible size (radius 18 -> ~1000 px; generous bounds for noise).
    assert labels[32, 32] == 1
    assert labels[0, 0] == 0
    fg = int(labels.sum())
    assert 400 < fg < 2000, f"implausible foreground size {fg}"
