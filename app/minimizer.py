"""Domain algorithm: canonical k-mers and window minimizers.

Conventions (fixed, mirrored in README and provenance records):

- 2-bit encoding A=0, C=1, G=2, T=3; the leftmost base is most significant.
- Canonical form: ``min(forward_int, reverse_complement_int)``. The strand
  is *kept* alongside the hash: ``+`` when forward <= reverse complement
  (palindromic k-mers included), ``-`` otherwise. Canonicalization decides
  which hash a k-mer contributes; it does not erase direction.
- Hash: named and seeded via :class:`app.config.MinimizerConfig`.
  ``mix64`` is a splitmix64-style finalizer, deterministic across runs and
  platforms. ``identity`` maps the canonical integer to itself and exists
  so tests can carry hand-computed expectations.
- Windows: every window is exactly ``window`` consecutive k-mers. No
  partial end windows are emitted; a sequence of length ``k + window - 1``
  yields exactly one window, a shorter one yields none.
- Minimizer of a window: the k-mer with the smallest hash. Ties are broken
  by taking the *rightmost* position.
- Dedup scope: consecutive windows *within one sequence* that resolve to
  the same minimizer position emit a single seed. Dedup never spans
  sequences, and non-consecutive repeats are all kept.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import MinimizerConfig
from .errors import InvalidParameterError, SequenceTooShortError

_BASE_TO_INT = {"A": 0, "C": 1, "G": 2, "T": 3}
_U64 = np.uint64
_MASK64 = np.uint64(0xFFFFFFFFFFFFFFFF)

# splitmix64 finalizer constants
_MIX_ADD = _U64(0x9E3779B97F4A7C15)
_MIX_MUL1 = _U64(0xBF58476D1CE4E5B9)
_MIX_MUL2 = _U64(0x94D049BB133111EB)


@dataclass(frozen=True)
class Minimizer:
    """One emitted seed: canonical hash, 0-based position, kept strand."""

    hash: int
    position: int
    strand: str  # "+" or "-"

    def to_dict(self) -> dict:
        return {"hash": self.hash, "position": self.position, "strand": self.strand}


def encode_sequence(sequence: str) -> np.ndarray:
    """Encode an ACGT string as uint8 2-bit codes (A=0 C=1 G=2 T=3)."""
    return np.frombuffer(
        bytes(_BASE_TO_INT[b] for b in sequence), dtype=np.uint8
    ).copy()


def kmer_forward_ints(codes: np.ndarray, k: int) -> np.ndarray:
    """Forward-strand integer of every k-mer, positions 0..n-k."""
    n_kmers = len(codes) - k + 1
    out = np.zeros(n_kmers, dtype=np.uint64)
    wide = codes.astype(np.uint64)
    for offset in range(k):
        out = (out << np.uint64(2)) | wide[offset : offset + n_kmers]
    return out


def reverse_complement_ints(forward: np.ndarray, k: int) -> np.ndarray:
    """Integer of the reverse complement for each forward k-mer integer."""
    rc = np.zeros_like(forward)
    x = forward.copy()
    three = np.uint64(3)
    for _ in range(k):
        rc = (rc << np.uint64(2)) | (three - (x & three))
        x = x >> np.uint64(2)
    return rc


def canonicalize(
    forward: np.ndarray, k: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (canonical_int, canonical_forward_int, strand) per k-mer.

    ``strand`` is ``True`` where the forward form is the canonical one
    (``forward <= reverse_complement``); direction information is preserved
    here even though downstream hashing uses only the canonical value.
    """
    rc = reverse_complement_ints(forward, k)
    canonical = np.minimum(forward, rc)
    is_forward = forward <= rc
    return canonical, forward, is_forward


def mix64(values: np.ndarray, seed: int = 0) -> np.ndarray:
    """Deterministic 64-bit mix (splitmix64 finalizer) over uint64 values."""
    x = values.astype(np.uint64) ^ np.uint64(seed)
    x = x + _MIX_ADD
    x = (x ^ (x >> np.uint64(30))) * _MIX_MUL1
    x = (x ^ (x >> np.uint64(27))) * _MIX_MUL2
    x = x ^ (x >> np.uint64(31))
    return x & _MASK64


def hash_canonical(canonical: np.ndarray, config: MinimizerConfig) -> np.ndarray:
    """Apply the configured named hash to canonical k-mer integers."""
    if config.hash_name == "mix64":
        return mix64(canonical, seed=config.hash_seed)
    if config.hash_name == "identity":
        return canonical.astype(np.uint64) & _MASK64
    # MinimizerConfig validates hash_name, so this is unreachable unless a
    # config object was forged; fail loudly rather than guessing.
    raise InvalidParameterError(  # pragma: no cover - defensive
        f"unsupported hash {config.hash_name!r}"
    )


def kmer_hashes(
    sequence: str, config: MinimizerConfig
) -> tuple[np.ndarray, np.ndarray]:
    """(hash, is_forward_strand) for every k-mer of ``sequence``."""
    codes = encode_sequence(sequence)
    forward = kmer_forward_ints(codes, config.k)
    canonical, _, is_forward = canonicalize(forward, config.k)
    return hash_canonical(canonical, config), is_forward


def window_minimizers(sequence: str, config: MinimizerConfig) -> list[Minimizer]:
    """Minimizer of every full window, in window order (no dedup).

    Raises :class:`SequenceTooShortError` when no full window exists; the
    caller decides whether that is an error (queries) or an empty result
    (indexing skips the record after logging it).
    """
    if len(sequence) < config.min_sequence_length:
        raise SequenceTooShortError(
            f"sequence length {len(sequence)} < k + window - 1 "
            f"({config.min_sequence_length}); no full window exists",
            detail={
                "length": len(sequence),
                "k": config.k,
                "window": config.window,
            },
        )
    hashes, is_forward = kmer_hashes(sequence, config)
    n_kmers = len(hashes)
    w = config.window
    out: list[Minimizer] = []
    for start in range(n_kmers - w + 1):
        window_hashes = hashes[start : start + w]
        minimum = window_hashes.min()
        # tie-break: rightmost position among equal hashes
        position = start + int(np.flatnonzero(window_hashes == minimum)[-1])
        out.append(
            Minimizer(
                hash=int(minimum),
                position=position,
                strand="+" if is_forward[position] else "-",
            )
        )
    return out


def compute_minimizers(sequence: str, config: MinimizerConfig) -> list[Minimizer]:
    """Emitted seeds: window minimizers with consecutive-position dedup.

    Dedup scope is exactly "consecutive windows of one sequence resolving
    to the same position"; the first occurrence is kept.
    """
    emitted: list[Minimizer] = []
    last_position: int | None = None
    for mini in window_minimizers(sequence, config):
        if mini.position != last_position:
            emitted.append(mini)
            last_position = mini.position
    return emitted
