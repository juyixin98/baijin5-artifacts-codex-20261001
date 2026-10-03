"""Synthetic sequence parsing boundary."""

from __future__ import annotations

import pytest

from njtree.errors import ErrorCategory, InputValidationError
from njtree.parsing import hamming_matrix, parse_fasta

from .conftest import FIXTURES_DIR, load_fixture


def test_fasta_fixture_produces_hand_computed_matrix():
    # The 11-site synthetic alignment was hand-designed so its Hamming
    # matrix equals the hand-computed additive4 matrix exactly.
    records = parse_fasta((FIXTURES_DIR / "sequences.fasta").read_text())
    dm = hamming_matrix(records)
    expected = load_fixture("additive4.json")
    assert list(dm.labels) == expected["labels"]
    assert dm.values.tolist() == [[float(v) for v in row] for row in expected["matrix"]]


def test_multiline_sequences_are_concatenated():
    records = parse_fasta(">A\nAC\nGT\n>B\nAC\nGC\n>C\nAC\nGA\n")
    assert records == [("A", "ACGT"), ("B", "ACGC"), ("C", "ACGA")]
    dm = hamming_matrix(records)
    assert dm.values[0, 1] == 1.0
    assert dm.values[0, 2] == 1.0
    assert dm.values[1, 2] == 1.0


@pytest.mark.parametrize("text,detail", [
    ("", None),
    ("   \n  ", None),
    ("ACGT\n>A\nACGT\n>B\nACGT\n>C\nACGT\n", "line"),          # data before header
    (">\nA\nACGT\n>B\nACGT\n>C\nACGT\n", "line"),              # empty label
    (">A B\nACGT\n>B\nACGT\n>C\nACGT\n", "label"),             # bad charset
    (">A\nACGX\n>B\nACGT\n>C\nACGT\n", "invalid"),             # invalid character
    (">A\nACGT\n>B\nACGT\n>A\nACGT\n", "duplicates"),          # duplicate labels
    (">A\nACGT\n>B\nACG\n>C\nACGT\n", "lengths"),              # unequal lengths
    (">A\n>B\nACGT\n>C\nACGT\n", "label"),                     # empty sequence
])
def test_malformed_fasta_raises_input_error(text, detail):
    with pytest.raises(InputValidationError) as excinfo:
        records = parse_fasta(text)
        hamming_matrix(records)
    assert excinfo.value.category is ErrorCategory.INPUT_ERROR
    if detail is not None:
        assert detail in str(excinfo.value.details) or detail in excinfo.value.message


def test_hamming_counts_mismatches_exactly():
    records = [("A", "AAAA"), ("B", "AAAT"), ("C", "TTTT")]
    dm = hamming_matrix(records)
    assert dm.values.tolist() == [[0, 1, 4], [1, 0, 3], [4, 3, 0]]
