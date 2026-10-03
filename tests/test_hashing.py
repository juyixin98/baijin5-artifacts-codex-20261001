"""Golden-vector tests for the fixed hash contract.

Expected constants were derived once by an independent integer implementation
(see tests/oracle.py and commit history), not by calling the code under test.
"""
from __future__ import annotations

from miniseed.hashing import HASH_VERSION, kmer_hash
from miniseed.sequence import canonicalize

from oracle import golden_hash


def test_hash_version_is_pinned():
    assert HASH_VERSION == "fnv1a-2bit-v1"


def test_single_base_golden_values():
    # Literal independent FNV-1a-2bit golden values (mod 2**63) for A/C/T.
    assert kmer_hash("A") == 3414781078840391647
    assert kmer_hash("C") == 3414779979328763436
    assert kmer_hash("T") == 3414782178352019858
    # G is cross-checked against the independently written oracle rather than
    # a pasted literal.
    assert kmer_hash("G") == golden_hash("G")


def test_9mer_golden_values():
    assert kmer_hash("AAAAAAAAA") == 7351143678008633791
    assert kmer_hash("ACGTACGTA") == 2370160783881501735


def test_hash_is_deterministic_and_nonnegative():
    for s in ("ACGTACGT", "GGGGCCCCA", "TTTTTTTTT"):
        v = kmer_hash(s)
        assert v == kmer_hash(s)
        assert 0 <= v < 2 ** 63


def test_canonical_reverse_complement_hashes_equal():
    rc = canonicalize("ACGTACGTA").canonical
    fwd = canonicalize("TACGTACGT").canonical  # reverse complement
    assert fwd == rc
    assert kmer_hash(fwd) == kmer_hash(rc) == golden_hash(rc)


def test_agrees_with_independent_oracle_on_mixed_kmers():
    kmers = [
        "ACG", "CGT", "GTT", "TTG", "TGC", "GCA", "AAC",
        "ACGTACGTA", "GTACGTACG", "GATTACACC",
    ]
    for kmer in kmers:
        canon = canonicalize(kmer).canonical
        assert kmer_hash(canon) == golden_hash(canon), kmer
