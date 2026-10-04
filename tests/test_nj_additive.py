"""Additive fixtures: the tree must be reconstructed exactly.

Reference values are hand-derived and stored in the fixtures; the
independent Newick parser (scripts/independent_newick.py) shares no code
with the service, so path-distance agreement is a genuine cross-check.
"""

from __future__ import annotations

import pytest

from app.newick import serialize_newick
from app.nj import neighbor_joining
from app.residuals import compute_residuals
from scripts.independent_newick import cherries, leaf_distances, parse_newick

TOL = 1e-9


def test_additive_4taxon_exact_reconstruction(load_fixture):
    fx = load_fixture("additive_4taxon.json")
    labels, matrix = fx["labels"], fx["matrix"]
    expected = fx["expected"]

    result = neighbor_joining(labels, matrix)
    newick, leaf_map = serialize_newick(result)

    assert newick == expected["newick"]
    assert leaf_map == expected["leaf_map"]

    residuals = compute_residuals(labels, matrix, result)
    assert residuals.sum_abs == pytest.approx(0.0, abs=TOL)
    assert residuals.max_abs == pytest.approx(0.0, abs=TOL)


def test_additive_4taxon_path_distances_via_independent_parser(load_fixture):
    fx = load_fixture("additive_4taxon.json")
    labels, matrix = fx["labels"], fx["matrix"]

    result = neighbor_joining(labels, matrix)
    newick, leaf_map = serialize_newick(result)

    distances = leaf_distances(parse_newick(newick))
    id_by_label = {label: leaf_id for leaf_id, label in leaf_map.items()}
    for i, li in enumerate(labels):
        for j, lj in enumerate(labels):
            if j <= i:
                continue
            a, b = sorted((id_by_label[li], id_by_label[lj]))
            assert distances[(a, b)] == pytest.approx(matrix[i][j], abs=TOL)


def test_additive_4taxon_cherries(load_fixture):
    fx = load_fixture("additive_4taxon.json")
    result = neighbor_joining(fx["labels"], fx["matrix"])
    newick, leaf_map = serialize_newick(result)
    found = {tuple(sorted(pair)) for pair in cherries(parse_newick(newick))}
    for pair in fx["expected"]["cherries"]:
        leaf_ids = tuple(sorted(f"L{fx['labels'].index(t)}" for t in pair))
        assert leaf_ids in found


def test_two_taxa_edge_case():
    labels = ["X", "Y"]
    matrix = [[0.0, 3.0], [3.0, 0.0]]
    result = neighbor_joining(labels, matrix)
    newick, leaf_map = serialize_newick(result)
    assert newick == "(L0:1.5,L1:1.5);"
    assert leaf_map == {"L0": "X", "L1": "Y"}
    residuals = compute_residuals(labels, matrix, result)
    assert residuals.sum_abs == pytest.approx(0.0, abs=TOL)


def test_input_matrix_not_mutated(load_fixture):
    fx = load_fixture("additive_4taxon.json")
    matrix = [row[:] for row in fx["matrix"]]
    snapshot = [row[:] for row in matrix]
    neighbor_joining(fx["labels"], matrix)
    assert matrix == snapshot
