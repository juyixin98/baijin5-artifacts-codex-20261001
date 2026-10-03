"""Minimizer algorithm: hand-computed cases + full-window enumeration.

Expected values in the hand-pinned tests are literals derived on paper
(identity hash = canonical 2-bit integer, A=0 C=1 G=2 T=3); the
enumeration tests compare against tests/reference.py, an independent
scalar implementation. Neither oracle comes from app/minimizer.py.
"""

import random

import pytest

from app.config import MinimizerConfig
from app.errors import SequenceTooShortError
from app.minimizer import Minimizer, compute_minimizers, window_minimizers

from .reference import reference_minimizers

IDENTITY = MinimizerConfig(k=3, window=2, hash_name="identity")


def as_tuples(minimizers: list[Minimizer]) -> list[tuple[int, int, str]]:
    return [(m.hash, m.position, m.strand) for m in minimizers]


def test_hand_computed_acgtac(runlog):
    # k-mers: ACG(6,+) CGT(6,-) GTA(44,+) TAC(44,-); windows of 2:
    # W0 {6,6} tie -> rightmost pos1; W1 {6,44} -> pos1 (dedup);
    # W2 {44,44} tie -> rightmost pos3.
    runlog.step(
        "judgment_basis",
        basis="hand-computed literals: canonical ints 6/6/44/44, "
        "tie-break rightmost, consecutive dedup drops W1",
    )
    got = compute_minimizers("ACGTAC", IDENTITY)
    assert as_tuples(got) == [(6, 1, "-"), (44, 3, "-")]


def test_homopolymer_long_run(runlog):
    # AAA... : every k-mer canonical 0 ('+'); ties move the minimizer one
    # step right per window, so all 5 window positions 1..5 are emitted.
    runlog.step(
        "judgment_basis",
        basis="homopolymer: all hashes equal, rightmost tie-break walks "
        "positions 1..5, no consecutive dedup possible",
    )
    got = compute_minimizers("AAAAAAAA", IDENTITY)
    assert as_tuples(got) == [(0, p, "+") for p in range(1, 6)]


def test_exact_boundary_single_window():
    # n == k + w - 1 -> exactly one full window, at position 0.
    cfg = MinimizerConfig(k=4, window=3, hash_name="identity")
    got = window_minimizers("ACGTAC", cfg)  # n = 6 = 4 + 3 - 1
    assert len(got) == 1
    assert got[0].position in (0, 1, 2)


def test_short_tail_window_not_emitted():
    # n == k + w - 2 -> no full window exists; a short tail window must
    # NOT be fabricated. Failure category: SEQUENCE_TOO_SHORT.
    cfg = MinimizerConfig(k=4, window=3, hash_name="identity")
    with pytest.raises(SequenceTooShortError) as excinfo:
        window_minimizers("ACGTA", cfg)  # n = 5 = 4 + 3 - 2
    assert excinfo.value.category == "SEQUENCE_TOO_SHORT"
    assert excinfo.value.detail["length"] == 5


def test_strand_preserved_by_canonicalization():
    # CGT: forward 27 > revcomp(ACG) 6 -> canonical hash of ACG but the
    # strand must stay '-'; ACG itself is '+'.
    cfg = MinimizerConfig(k=3, window=1, hash_name="identity")
    (minus,) = compute_minimizers("CGT", cfg)
    (plus,) = compute_minimizers("ACG", cfg)
    assert minus.hash == plus.hash == 6
    assert minus.strand == "-" and plus.strand == "+"


def test_palindromic_kmer_tie_defaults_to_forward():
    cfg = MinimizerConfig(k=4, window=1, hash_name="identity")
    (mini,) = compute_minimizers("ACGT", cfg)  # forward == revcomp == 27
    assert mini.hash == 27
    assert mini.strand == "+"


def test_dedup_scope_is_consecutive_windows(runlog):
    # identity hashes per k-mer: CC=5 CA=4 AA=0 AC=1 CC=5
    # windows: [5,4]->1, [4,0]->2, [0,1]->2 (dup, dropped), [1,5]->3
    runlog.step(
        "judgment_basis",
        basis="hand-computed: window positions [1,2,2,3]; dedup removes "
        "only the consecutive repeat at position 2",
    )
    cfg = MinimizerConfig(k=2, window=2, hash_name="identity")
    assert [m.position for m in window_minimizers("CCAACC", cfg)] == [1, 2, 2, 3]
    assert [m.position for m in compute_minimizers("CCAACC", cfg)] == [1, 2, 3]


def test_nonconsecutive_repeats_are_kept():
    # Same minimizer position value recurring non-consecutively is not
    # deduped (dedup scope is consecutive windows only).
    cfg = MinimizerConfig(k=2, window=2, hash_name="identity")
    got = compute_minimizers("CCAACC", cfg)
    assert [m.hash for m in got] == [4, 0, 1]


@pytest.mark.parametrize("hash_name", ["mix64", "identity"])
@pytest.mark.parametrize("seed_value", [0, 7])
def test_full_window_enumeration_matches_reference(hash_name, seed_value, runlog):
    rng = random.Random(20261003)
    runlog.step(
        "judgment_basis",
        basis="independent scalar oracle in tests/reference.py",
        hash_name=hash_name,
        seed=seed_value,
    )
    for trial in range(25):
        k, w = 3 + trial % 3, 1 + trial % 4
        n = k + w - 1 + rng.randrange(0, 40)
        seq = "".join(rng.choice("ACGT") for _ in range(n))
        cfg = MinimizerConfig(
            k=k, window=w, hash_name=hash_name, hash_seed=seed_value
        )
        expected = reference_minimizers(seq, k, w, hash_name, seed_value)
        got = as_tuples(compute_minimizers(seq, cfg))
        assert got == expected, f"trial {trial}: seq={seq} k={k} w={w}"
        runlog.step(
            "enumeration_trial",
            trial=trial,
            n=n,
            k=k,
            w=w,
            seeds=len(got),
            match=True,
        )
