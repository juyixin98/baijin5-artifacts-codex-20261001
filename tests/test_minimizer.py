"""Minimizer core tests.

Strategy:

* cross-check the production deque scanner against a separately written
  brute-force full-window oracle on many short sequences (property-style);
* assert CONCRETE results for the required cases: same-value long run,
  tail (single-window) read, reverse-complement read, equal-hash tie rule,
  N-only windows, dedup scope.
"""
from __future__ import annotations

import itertools

import pytest

from miniseed.errors import ErrorCode, MiniseedError
from miniseed.minimizer import minimum_length, scan_minimizers, validate_params
from miniseed.sequence import parse_sequence

from oracle import golden_minimizers, golden_window_picks


def _scan(seq: str, k: int, w: int):
    return scan_minimizers(parse_sequence(seq).sequence, k, w)


# -------------------------------------------------- oracle cross-check
@pytest.mark.parametrize(
    "seq,k,w",
    [
        ("ACGTTGCAACGTACG", 3, 4),
        ("ACGTACGTACGT", 3, 3),
        ("GATTACAGATTACAGATTACA", 5, 4),
        ("AAAACGTAAAACGT", 4, 3),
        ("TTTTTTTTTTT", 3, 4),
        ("ACGTACGTACGTACGT", 9, 5),
        ("NACGTACGTNACGTAC", 3, 3),
        ("GATTACAGATTACAGATTACAGATTACA", 7, 5),
    ],
)
def test_scanner_matches_independent_oracle(seq, k, w):
    result = _scan(seq, k, w)
    expected = golden_minimizers(seq, k, w)
    actual = [
        {
            "offset": m.offset,
            "window_index": m.window_index,
            "hash": m.hash,
            "canonical": m.canonical,
            "orientation": m.orientation,
        }
        for m in result.minimizers
    ]
    assert actual == expected
    # Window accounting must match the oracle's per-window enumeration.
    picks = golden_window_picks(seq, k, w)
    assert result.total_windows == len(picks)
    assert result.windows_without_kmer == sum(p is None for p in picks)


def test_exhaustive_small_alphabet_matches_oracle():
    """Enumerate every length-10 2-letter AC/GT word at k=3,w=4."""
    for letters in ("AC", "AG", "AT", "CG", "CT", "GT"):
        for tup in itertools.product(letters, repeat=10):
            seq = "".join(tup)
            actual = [
                (m.offset, m.hash) for m in _scan(seq, 3, 4).minimizers
            ]
            expected = [
                (e["offset"], e["hash"]) for e in golden_minimizers(seq, 3, 4)
            ]
            assert actual == expected, seq


# -------------------------------------------------- concrete contracts
def test_same_value_long_run_collapses_to_one_seed():
    # Homopolymer: every window selects the same 3-mer value. Adjacent-run
    # dedup by VALUE must leave exactly one record at offset 0.
    result = _scan("AAAAAAAAAA", 3, 4)  # 5 windows (8 k-mers)
    assert result.total_windows == 5
    assert len(result.minimizers) == 1
    m = result.minimizers[0]
    assert m.offset == 0
    assert m.window_index == 0
    assert m.canonical == "AAA"
    assert result.distinct_hashes == 1


def test_tail_short_single_window_read():
    # Length k+w-1 -> exactly one window; the minimum is over all w k-mers.
    seq = "ACGTACGTACGTACG"  # length 15 = k + w - 1 -> one window
    k, w = 5, 11
    assert len(seq) == minimum_length(k, w)
    result = _scan(seq, k, w)
    assert result.total_windows == 1
    picks = golden_window_picks(seq, k, w)
    assert len(picks) == 1 and picks[0] is not None
    hv, off = picks[0]
    assert len(result.minimizers) == 1
    assert result.minimizers[0].offset == off
    assert result.minimizers[0].hash == hv


def test_reverse_complement_read_produces_canonical_seeds():
    seq = "ACGTTGCAACGTACG"
    comp = {"A": "T", "C": "G", "G": "C", "T": "A"}
    rc = "".join(comp[b] for b in reversed(seq))
    forward = _scan(seq, 3, 4)
    reverse = _scan(rc, 3, 4)
    # Same multiset of canonical seed values regardless of strand.
    fwd_values = sorted(m.hash for m in forward.minimizers)
    rev_values = sorted(m.hash for m in reverse.minimizers)
    assert fwd_values == rev_values
    # Orientations must be flipped where a k-mer is not a palindrome.
    assert any(m.orientation == "-" for m in reverse.minimizers)


def test_leftmost_offset_wins_equal_hashes():
    # All identical k-mers share a hash; the first window's selected offset
    # must be the leftmost eligible offset (0), never a later one.
    result = _scan("CCCCCCCCCC", 3, 5)
    assert result.minimizers[0].offset == 0


def test_n_only_window_breaks_adjacency_and_is_counted():
    # k=3,w=2. N block makes the window spanning only N-k-mers eligible-empty,
    # splitting adjacency.
    seq = "ACGNNNACG"
    result = _scan(seq, 3, 2)
    assert result.windows_without_kmer >= 1
    # The same canonical value flanking the N gap yields separate records.
    hashes = [m.hash for m in result.minimizers]
    assert len(hashes) >= 2


def test_repeated_value_non_adjacent_is_retained():
    # A value that leaves the window and returns later starts a new record.
    seq = "AAAACGTTTTAAAACGT"  # k=4,w=3
    result = _scan(seq, 4, 3)
    expected = golden_minimizers(seq, 4, 3)
    assert [m.offset for m in result.minimizers] == [
        e["offset"] for e in expected
    ]
    # At least one hash value appears in two separate records (non-adjacent).
    seen: dict[int, int] = {}
    for m in result.minimizers:
        seen[m.hash] = seen.get(m.hash, 0) + 1
    assert any(count >= 2 for count in seen.values())


# -------------------------------------------------- parameter errors
def test_sequence_too_short_has_category():
    with pytest.raises(MiniseedError) as exc:
        _scan("ACGT", 9, 5)
    assert exc.value.code is ErrorCode.SEQUENCE_TOO_SHORT
    assert exc.value.context["length"] == 4


@pytest.mark.parametrize(
    "k,w,code",
    [
        (0, 5, ErrorCode.INVALID_PARAMETER),
        (9, 0, ErrorCode.INVALID_PARAMETER),
        (-1, 5, ErrorCode.INVALID_PARAMETER),
        (32, 5, ErrorCode.PARAMETER_CONFLICT),
    ],
)
def test_invalid_parameter_categories(k, w, code):
    with pytest.raises(MiniseedError) as exc:
        validate_params(k, w)
    assert exc.value.code is code


def test_empty_sequence_category():
    with pytest.raises(MiniseedError) as exc:
        parse_sequence("   \n  ")
    assert exc.value.code is ErrorCode.EMPTY_SEQUENCE


def test_invalid_character_category():
    with pytest.raises(MiniseedError) as exc:
        parse_sequence("ACGTXACGT")
    assert exc.value.code is ErrorCode.INVALID_CHARACTER
    assert exc.value.context["character"] == "X"
