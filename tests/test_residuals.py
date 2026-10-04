"""Residual reporting: independent recomputation from the emitted Newick."""

from __future__ import annotations

import pytest

from app.newick import serialize_newick
from app.nj import neighbor_joining
from app.residuals import compute_residuals, patristic_distance
from scripts.independent_newick import leaf_distances, parse_newick

TOL = 1e-9


def _independent_sum_abs(newick, leaf_map, labels, matrix):
    distances = leaf_distances(parse_newick(newick))
    id_by_label = {label: leaf_id for leaf_id, label in leaf_map.items()}
    total = 0.0
    for i, li in enumerate(labels):
        for j in range(i + 1, len(labels)):
            a, b = sorted((id_by_label[li], id_by_label[labels[j]]))
            total += abs(matrix[i][j] - distances[(a, b)])
    return total


def test_noisy_matrix_reports_positive_residual_matching_independent_check(
    load_fixture,
):
    fx = load_fixture("noisy_5taxon.json")
    labels, matrix = fx["labels"], fx["matrix"]
    result = neighbor_joining(labels, matrix)
    newick, leaf_map = serialize_newick(result)
    residuals = compute_residuals(labels, matrix, result)

    assert residuals.sum_abs > 0.0  # non-additive input cannot fit exactly
    independent = _independent_sum_abs(newick, leaf_map, labels, matrix)
    assert residuals.sum_abs == pytest.approx(independent, abs=TOL)

    # Aggregate statistics are consistent with the per-pair table.
    diffs = [p.abs_diff for p in residuals.per_pair]
    assert len(diffs) == len(labels) * (len(labels) - 1) // 2
    assert residuals.sum_abs == pytest.approx(sum(diffs), abs=TOL)
    assert residuals.max_abs == pytest.approx(max(diffs), abs=TOL)
    assert residuals.mean_abs == pytest.approx(sum(diffs) / len(diffs), abs=TOL)
    assert residuals.rms == pytest.approx(
        (sum(d * d for d in diffs) / len(diffs)) ** 0.5, abs=TOL
    )
    for pair in residuals.per_pair:
        assert pair.abs_diff == pytest.approx(abs(pair.observed - pair.tree), abs=TOL)


def test_additive_matrix_residual_is_exactly_zero(load_fixture):
    fx = load_fixture("additive_4taxon.json")
    result = neighbor_joining(fx["labels"], fx["matrix"])
    residuals = compute_residuals(fx["labels"], fx["matrix"], result)
    assert residuals.sum_abs == pytest.approx(0.0, abs=TOL)
    assert residuals.rms == pytest.approx(0.0, abs=TOL)


def test_patristic_distance_disconnected_raises():
    with pytest.raises(KeyError):
        patristic_distance({0: {1: 1.0}, 1: {0: 1.0}}, 0, 99)


def test_patristic_distance_same_node_is_zero():
    assert patristic_distance({}, 7, 7) == 0.0
