"""Duplicate leaves: identical sequences cluster; duplicate labels rejected."""

from __future__ import annotations

import pytest

from app.newick import serialize_newick
from app.nj import neighbor_joining
from app.parsing import p_distance_matrix, parse_fasta
from app.residuals import compute_residuals
from scripts.independent_newick import cherries, parse_newick

TOL = 1e-9


def test_identical_sequences_form_zero_length_cherry(load_fixture):
    fx = load_fixture("duplicate_leaves.json")
    labels, matrix = p_distance_matrix(parse_fasta(fx["fasta"]))

    # The two identical sequences are at distance 0 from each other.
    assert matrix[0][1] == 0.0

    result = neighbor_joining(labels, matrix)
    newick, leaf_map = serialize_newick(result)
    assert newick == fx["expected"]["newick"]
    assert leaf_map == fx["expected"]["leaf_map"]

    tree = parse_newick(newick)
    cherry_ids = {tuple(sorted(pair)) for pair in cherries(tree)}
    assert ("L0", "L1") in cherry_ids
    # Both limbs of the duplicate cherry are exactly zero.
    assert ":0," in newick and ":0)" in newick

    residuals = compute_residuals(labels, matrix, result)
    assert residuals.sum_abs == pytest.approx(
        fx["expected"]["residual_sum_abs"], abs=TOL
    )


def test_duplicate_labels_are_an_input_error_not_a_silent_merge(client):
    payload = {
        "input_format": "matrix",
        "matrix": {
            "labels": ["a", "a", "b"],
            "distances": [[0, 1, 2], [1, 0, 3], [2, 3, 0]],
        },
    }
    response = client.post("/v1/trees", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "INPUT_VALIDATION"
    assert "duplicate" in response.json()["error"]["message"]
