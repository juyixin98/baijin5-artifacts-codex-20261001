"""Independent reference implementation for tests.

Deliberately written *differently* from app/minimizer.py: pure-Python
strings and scalar arithmetic, no NumPy, no shared code. Tests compare the
vectorized core against this oracle, and tiny cases are additionally
pinned to hand-computed literals — so expected answers are not generated
by the implementation under test.
"""

from __future__ import annotations

_MASK = (1 << 64) - 1
_COMPLEMENT = {"A": "T", "C": "G", "G": "C", "T": "A"}


def revcomp(seq: str) -> str:
    return "".join(_COMPLEMENT[b] for b in reversed(seq))


def encode_kmer(kmer: str) -> int:
    value = 0
    for base in kmer:
        value = value * 4 + "ACGT".index(base)
    return value


def mix64_scalar(x: int, seed: int = 0) -> int:
    x = (x ^ seed) & _MASK
    x = (x + 0x9E3779B97F4A7C15) & _MASK
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _MASK
    x = (x ^ (x >> 31)) & _MASK
    return x


def _canonical(kmer: str) -> tuple[int, str]:
    fwd = encode_kmer(kmer)
    rev = encode_kmer(revcomp(kmer))
    return (min(fwd, rev), "+" if fwd <= rev else "-")


def reference_window_minimizers(
    seq: str, k: int, w: int, hash_name: str = "mix64", seed: int = 0
) -> list[tuple[int, int, str]]:
    """(hash, position, strand) per full window; tie-break rightmost."""
    if len(seq) < k + w - 1:
        raise ValueError("sequence shorter than k + w - 1")
    kmers = [seq[i : i + k] for i in range(len(seq) - k + 1)]
    canon = [_canonical(km) for km in kmers]
    if hash_name == "mix64":
        hashes = [mix64_scalar(c, seed) for c, _ in canon]
    elif hash_name == "identity":
        hashes = [c for c, _ in canon]
    else:
        raise ValueError(f"unknown hash {hash_name!r}")
    out = []
    for start in range(len(hashes) - w + 1):
        window = hashes[start : start + w]
        minimum = min(window)
        pos = start + max(i for i, v in enumerate(window) if v == minimum)
        out.append((hashes[pos], pos, canon[pos][1]))
    return out


def reference_minimizers(
    seq: str, k: int, w: int, hash_name: str = "mix64", seed: int = 0
) -> list[tuple[int, int, str]]:
    """Window minimizers with consecutive-position dedup (per sequence)."""
    emitted: list[tuple[int, int, str]] = []
    last_pos = None
    for h, pos, strand in reference_window_minimizers(seq, k, w, hash_name, seed):
        if pos != last_pos:
            emitted.append((h, pos, strand))
            last_pos = pos
    return emitted
