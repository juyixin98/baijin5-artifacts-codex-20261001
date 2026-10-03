"""End-to-end test on the shipped synthetic fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from graphcut import AppConfig
from graphcut.api import create_app

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(AppConfig(data_dir=DATA_DIR)))


@pytest.fixture(scope="module")
def sample_spec() -> dict:
    return json.loads((DATA_DIR / "sample_spec.json").read_text())


def test_fixtures_exist():
    for name in ("sample_circle.png", "sample_seeds.json",
                 "sample_spec.json", "sample_ground_truth.npy"):
        assert (DATA_DIR / name).is_file(), f"missing fixture {name}"


def test_segment_recovers_disk(client, sample_spec):
    response = client.post("/v1/segment", json=sample_spec)
    assert response.status_code == 200, response.json()
    result = response.json()["result"]

    labeling = np.asarray(result["labeling"])
    truth = np.load(DATA_DIR / "sample_ground_truth.npy")
    intersection = np.logical_and(labeling == 1, truth == 1).sum()
    union = np.logical_or(labeling == 1, truth == 1).sum()
    iou = intersection / union
    assert iou > 0.9, f"segmentation IoU {iou:.3f} below 0.9"

    cert = result["certificate"]
    assert cert["consistent"] is True
    assert cert["seeds_satisfied"] is True
    energy = result["energy"]
    assert energy["total"] == pytest.approx(
        energy["data"] + energy["smoothness"]
    )


def test_validate_endpoint_scores_sample_labeling(client, sample_spec):
    result = client.post("/v1/segment", json=sample_spec).json()["result"]
    response = client.post("/v1/validate", json={
        "spec": sample_spec,
        "labeling": result["labeling"],
        "claimed_energy": result["energy"]["total"],
    })
    assert response.status_code == 200
    assert response.json()["matches_claim"] is True
