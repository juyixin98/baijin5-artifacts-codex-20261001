"""Additive matrices: exact structure recovery, path distances, leaf identity."""

from __future__ import annotations

import pytest

from njtree import (
    BuildParams,
    TreeService,
    validate_distance_matrix,
)
from njtree.nj import neighbor_joining
from njtree.tree import leaf_map, patristic_distances, to_newick

from . import independent
from .conftest import FIXTURES_DIR, load_fixture

TOL = 1e-9


def test_additive4_exact_newick_and_leaf_map():
    fx = load_fixture("additive4.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    root, steps, events = neighbor_joining(dm, run_id="t-additive4")
    assert to_newick(root) == fx["expected"]["newick"]
    assert leaf_map(root) == fx["expected"]["leaf_map"]
    assert events == []


def test_additive4_path_distances_match_hand_computed():
    # Independent check: parse the emitted Newick with the test-side parser
    # and compare every leaf-pair path distance against hand-computed values.
    fx = load_fixture("additive4.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    root, _, _ = neighbor_joining(dm)
    paths = independent.leaf_path_lengths(independent.parse_newick(to_newick(root)))
    for pair, expected in fx["expected"]["path_distances"].items():
        a, b = pair.split("|")
        assert paths[frozenset((a, b))] == pytest.approx(expected, abs=TOL), pair


def test_additive4_first_join_matches_hand_computation():
    fx = load_fixture("additive4.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    _, steps, _ = neighbor_joining(dm)
    first = steps[0]
    assert first.chosen_labels == fx["expected"]["first_step"]["chosen_labels"]
    assert first.q_value == pytest.approx(fx["expected"]["first_step"]["q_value"])
    assert first.tie_count == fx["expected"]["first_step"]["tie_count"]
    assert first.limbs == [pytest.approx(1.0), pytest.approx(2.0)]
    assert steps[-1].is_final


def test_additive6_zero_residual_and_topology():
    fx = load_fixture("additive6.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    root, _, events = neighbor_joining(dm)
    assert events == []
    newick = to_newick(root)
    # Topology: identical nontrivial unrooted splits as the true tree,
    # both parsed by the independent test-side parser.
    inferred_splits = independent.splits(independent.parse_newick(newick))
    true_splits = independent.splits(independent.parse_newick(fx["expected"]["true_tree_newick"]))
    assert inferred_splits == true_splits
    # Distances: total residual against the input matrix is zero.
    paths = independent.leaf_path_lengths(independent.parse_newick(newick))
    assert independent.total_residual(fx["labels"], fx["matrix"], paths) == pytest.approx(0.0, abs=TOL)


def test_fasta_end_to_end_matches_additive4():
    fx = load_fixture("additive4.json")
    service = TreeService()
    result = service.build_from_fasta(
        (FIXTURES_DIR / "sequences.fasta").read_text(), BuildParams())
    assert result.newick == fx["expected"]["newick"]
    assert result.leaf_map == fx["expected"]["leaf_map"]
    assert result.residuals.total_absolute == pytest.approx(0.0, abs=TOL)


def test_duplicate_leaves_become_zero_length_sisters():
    # d(D,E)=0 makes the cherry topology non-identifiable: any exact fit
    # co-locates D and E. The contract we assert is co-location at the same
    # attachment node with zero-length branches and zero residual.
    fx = load_fixture("duplicates.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    root, _, _ = neighbor_joining(dm)
    newick = to_newick(root)
    parsed = independent.parse_newick(newick)
    att = independent.attachments(parsed)
    d_parent, d_len = att["D"]
    e_parent, e_len = att["E"]
    assert d_parent == e_parent, f"D and E attach at different nodes in {newick}"
    assert [d_len, e_len] == fx["expected"]["sister_branch_lengths"]
    # Both duplicates keep distinct identities in the leaf map.
    lmap = leaf_map(root)
    assert lmap["D"] != lmap["E"]
    assert set(lmap) == {"A", "B", "C", "D", "E"}
    # The duplicate matrix is additive: zero residual.
    paths = independent.leaf_path_lengths(parsed)
    assert independent.total_residual(fx["labels"], fx["matrix"], paths) == pytest.approx(0.0, abs=TOL)


def test_patristic_distances_cover_all_pairs():
    fx = load_fixture("additive6.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    root, _, _ = neighbor_joining(dm)
    dists = patristic_distances(root)
    assert len(dists) == 6 * 5 // 2
