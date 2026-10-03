"""Unit tests for near-duplicate weighting.

Core claim under test: copying a sequence must NOT amplify its evidence.
Three identical copies + one variant must carry exactly the same total
weight as one copy + one variant.
"""

import numpy as np
import pytest
from conftest import judgement, read_fixture

from msa_backend.domain.weights import compute_weights, pairwise_identity
from msa_backend.parsing.fasta import parse_fasta_alignment

GAP = "-"
THRESHOLD = 0.95


def _weights_for(fixture: str):
    alignment = parse_fasta_alignment(read_fixture(fixture), GAP)
    return compute_weights(alignment, THRESHOLD, GAP)


def test_duplicate_copies_share_one_cluster():
    weights_by_id, clusters, vector = _weights_for("duplicates.fa")
    judgement(
        "duplicates.fa", "weights",
        "3 identical copies + 1 variant -> 2 clusters, total weight 2.0",
    )
    assert len(clusters) == 2
    assert sorted(len(c) for c in clusters) == [1, 3]
    assert vector.sum() == pytest.approx(2.0)
    assert weights_by_id["ref"] == pytest.approx(1.0 / 3.0)
    assert weights_by_id["dup1"] == pytest.approx(1.0 / 3.0)
    assert weights_by_id["dup2"] == pytest.approx(1.0 / 3.0)
    assert weights_by_id["var"] == pytest.approx(1.0)


def test_weight_total_matches_pair_without_duplicates():
    _, _, dup_vector = _weights_for("duplicates.fa")
    _, _, pair_vector = _weights_for("duplicates_pair.fa")
    judgement(
        "duplicates.fa vs duplicates_pair.fa", "weights",
        "adding near-duplicate copies must not change the total weight",
    )
    assert dup_vector.sum() == pytest.approx(pair_vector.sum())


def test_distinct_sequences_get_full_weight():
    weights_by_id, clusters, vector = _weights_for("diverse.fa")
    judgement("diverse.fa", "weights", "4 unrelated sequences -> 4 clusters of weight 1")
    assert len(clusters) == 4
    assert vector.sum() == pytest.approx(4.0)
    assert all(w == pytest.approx(1.0) for w in weights_by_id.values())


def test_gappy_fixture_weights_are_gap_aware():
    weights_by_id, clusters, _ = _weights_for("gappy.fa")
    judgement(
        "gappy.fa", "weights",
        "s1==s4 cluster (0.5 each); s2/s3 differ at compared positions -> weight 1.0",
    )
    assert weights_by_id["s1"] == pytest.approx(0.5)
    assert weights_by_id["s4"] == pytest.approx(0.5)
    assert weights_by_id["s2"] == pytest.approx(1.0)
    assert weights_by_id["s3"] == pytest.approx(1.0)


def test_pairwise_identity_ignores_double_gap_positions():
    judgement(
        "synthetic-rows", "identity",
        "positions where either row is a gap are excluded from comparison",
    )
    assert pairwise_identity("AC-T", "ACGT", GAP) == pytest.approx(1.0)
    assert pairwise_identity("AC-T", "AC-A", GAP) == pytest.approx(2 / 3)
    assert pairwise_identity("----", "--AA", GAP) == pytest.approx(1.0)


def test_weight_vector_is_immutable_input_to_columns():
    """The column layer must receive the weights, not recompute them."""
    _, _, vector = _weights_for("conserved.fa")
    copied = np.array(vector, copy=True)
    judgement("conserved.fa", "immutability", "copied weight vector equals original")
    assert np.array_equal(vector, copied)
