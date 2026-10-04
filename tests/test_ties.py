"""Tie-breaking: stable, recorded, and replayable."""

from __future__ import annotations

from app.newick import serialize_newick
from app.nj import neighbor_joining


def _tie_events(result):
    return [e for e in result.events if e["type"] == "q_tie"]


def test_ties_are_recorded_with_candidates_and_choice(load_fixture):
    fx = load_fixture("additive_4taxon.json")
    result = neighbor_joining(fx["labels"], fx["matrix"])
    ties = _tie_events(result)

    expected = fx["expected"]["ties"]
    assert len(ties) == len(expected)
    for event, ref in zip(ties, expected):
        assert event["round"] == ref["round"]
        assert event["data"]["candidates"] == ref["candidates"]
        assert event["data"]["chosen"] == ref["chosen"]
        assert event["data"]["rule"] == "first_in_enumeration_order"
        assert "q" in event["data"]


def test_tie_breaks_are_deterministic_across_runs(load_fixture):
    fx = load_fixture("noisy_5taxon.json")
    outputs = set()
    for _ in range(5):
        result = neighbor_joining(fx["labels"], fx["matrix"])
        outputs.add(serialize_newick(result)[0])
    assert len(outputs) == 1


def test_tie_winner_is_lowest_node_id_pair():
    # Q minima at (1,2) and (0,3): enumeration order guarantees (0,3) wins
    # regardless of dict/iteration accidents. Hand-computed matrix:
    # r = [10, 10, 10, 10]; Q(i,j) = 2*d(i,j) - 20, so the two smallest
    # off-diagonal entries tie; place them at (0,3) and (1,2).
    labels = ["A", "B", "C", "D"]
    matrix = [
        [0.0, 5.0, 5.0, 4.0],
        [5.0, 0.0, 4.0, 5.0],
        [5.0, 4.0, 0.0, 5.0],
        [4.0, 5.0, 5.0, 0.0],
    ]
    result = neighbor_joining(labels, matrix)
    ties = _tie_events(result)
    assert len(ties) >= 1
    first = ties[0]
    assert first["data"]["candidates"] == [[0, 3], [1, 2]]
    assert first["data"]["chosen"] == [0, 3]
