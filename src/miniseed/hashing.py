"""Deterministic k-mer hashing.

The hash contract is fixed and versioned:

* bases are two-bit encoded (A=0, C=1, G=2, T=3);
* hashing is FNV-1a over the encoded bytes, seeded with a fixed 64-bit
  offset basis, then reduced modulo 2**63 so values are JSON-safe;
* canonical k-mers are hashed, so a k-mer and its reverse complement map to
  the same value.

Golden vectors in the test suite pin this function; changing any constant
below is an algorithm-version change (bump :data:`HASH_VERSION`).
"""
from __future__ import annotations

from .config import HASH_MOD, HASH_SEED
from .sequence import _BASE_CODE

FNV_PRIME = 0x100000001B3
MASK64 = 0xFFFFFFFFFFFFFFFF
HASH_VERSION = "fnv1a-2bit-v1"


def encode_bases(kmer: str) -> bytes:
    """Two-bit encode a (canonical, ACGT-only) k-mer to packed bytes."""
    codes = bytearray(len(kmer))
    for i, base in enumerate(kmer):
        codes[i] = _BASE_CODE[base]
    return bytes(codes)


def kmer_hash(kmer: str) -> int:
    """Return the fixed FNV-1a hash of a canonical k-mer."""
    h = HASH_SEED
    for code in encode_bases(kmer):
        h ^= code
        h = (h * FNV_PRIME) & MASK64
    return h % HASH_MOD
