"""Tie-breaking: equal Q values resolve deterministically by the declared rule."""

from __future__ import annotations

from njtree import validate_distance_matrix
from njtree.nj import neighbor_joining
from njtree.tree import to_newick

from .conftest import load_fixture


def test_equal_q_tie_resolves_to_lexicographically_smallest_pair():
    # additive4 step 0: Q(A,B) == Q(C,D) == -28 exactly; the declared rule
    # (smallest node-id pair) must pick (A,B), and the tie must be reported.
    fx = load_fixture("additive4.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    _, steps, _ = neighbor_joining(dm)
    assert steps[0].tie_count == 2
    assert steps[0].chosen_labels == ["A", "B"]


def test_repeated_runs_are_byte_identical():
    fx = load_fixture("additive4.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    first = to_newick(neighbor_joining(dm)[0])
    for _ in range(5):
        assert to_newick(neighbor_joining(dm)[0]) == first


def test_four_way_tie_in_negative_fixture():
    fx = load_fixture("negative_branch.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    _, steps, _ = neighbor_joining(dm)
    assert steps[0].tie_count == fx["expected"]["first_step"]["tie_count"]
    assert steps[0].chosen_labels == fx["expected"]["first_step"]["chosen_labels"]
    assert steps[0].q_value == fx["expected"]["first_step"]["q_value"]


def test_tie_break_depends_only_on_input_order_not_dict_order():
    # Same matrix submitted twice through the full service must give the same
    # tree; stability is w.r.t. the declared input label order.
    from njtree import BuildParams, TreeService

    fx = load_fixture("additive4.json")
    service = TreeService()
    r1 = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    r2 = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    assert r1.newick == r2.newick
