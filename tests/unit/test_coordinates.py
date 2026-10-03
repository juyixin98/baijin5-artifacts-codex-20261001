"""Unit tests for alignment-column -> original-coordinate mapping."""

from conftest import judgement, read_fixture

from msa_backend.config import load_config
from msa_backend.parsing.fasta import parse_fasta_alignment
from msa_backend.pipeline import compute_alignment


def test_insertion_columns_keep_original_coordinates():
    config = load_config()
    alignment = parse_fasta_alignment(
        read_fixture("gappy.fa"), config.algorithm.gap_symbol
    )
    result = compute_alignment(alignment, config)
    judgement(
        "gappy.fa", "coordinate-map",
        "s2='AC--ACGA': gap columns map to None, residues keep 1-based original positions",
    )
    assert result.coordinate_maps["s2"] == (1, 2, None, None, 3, 4, 5, 6)
    assert result.coordinate_maps["s3"] == (1, None, None, None, 2, 3, 4, 5)
    assert result.coordinate_maps["s1"] == (1, 2, 3, 4, 5, 6, 7, 8)


def test_coordinate_map_covers_every_column():
    config = load_config()
    alignment = parse_fasta_alignment(
        read_fixture("conserved.fa"), config.algorithm.gap_symbol
    )
    result = compute_alignment(alignment, config)
    judgement("conserved.fa", "coordinate-map", "one entry per alignment column per sequence")
    for seq_id in result.sequence_ids:
        assert len(result.coordinate_maps[seq_id]) == alignment.n_columns
