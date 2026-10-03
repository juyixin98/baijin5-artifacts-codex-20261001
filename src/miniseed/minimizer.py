"""Minimizer selection (the domain core).

For a sequence and parameters ``(k, w)`` each length-``w`` window of k-mers
contributes exactly one minimizer: the k-mer with the smallest hash.

Fixed contract (behavioral guarantee #1):

* k and the window length w are validated up front and must be positive with
  ``w >= 1``; sequences shorter than ``k + w - 1`` cannot cover a window and
  raise ``SEQUENCE_TOO_SHORT``;
* hashes come from the versioned FNV-1a scheme in :mod:`miniseed.hashing`;
* equal-hash ties are broken by the *leftmost* k-mer offset in the window
  (deterministic, independent of dict ordering);
* reverse-complement normalization is applied before hashing
  (see :mod:`miniseed.sequence`), so seeding is strand-agnostic while the
  orientation is preserved per record.

Deduplication scope (behavioral guarantee #2):

* minimizers are emitted once per maximal run of **adjacent windows
  selecting the same minimizer value (hash)**, at the offset of its first
  selection ("first-occurrence" dedup by value). A homopolymer therefore
  yields one seed even though the minimum shifts offsets each window; a
  window with no N-free k-mer breaks adjacency; and a value that leaves the
  window and re-enters later is retained as a new record -- the scope is
  strictly the adjacent run, never the whole sequence;
* low-complexity outbreaks (e.g. homopolymers, where every window has the same
  minimizer) collapse to a single record here, and any residual oversized hash
  bucket is rejected at index time (``BUCKET_OVERFLOW``).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .errors import ErrorCode, MiniseedError
from .hashing import HASH_VERSION, kmer_hash
from .sequence import CanonicalKmer, iter_kmers


def validate_params(k: int, w: int) -> None:
    """Validate the ``(k, w)`` parameter pair or raise a categorized error."""
    # bool is an int subclass; reject it explicitly.
    if isinstance(k, bool) or isinstance(w, bool) or not isinstance(k, int) \
            or not isinstance(w, int):
        raise MiniseedError(
            ErrorCode.INVALID_PARAMETER,
            "k and w must be integers",
            context={"k": repr(k), "w": repr(w)},
        )
    if k <= 0:
        raise MiniseedError(
            ErrorCode.INVALID_PARAMETER,
            "k must be a positive integer",
            context={"k": k},
        )
    if w <= 0:
        raise MiniseedError(
            ErrorCode.INVALID_PARAMETER,
            "w (window length in k-mers) must be a positive integer",
            context={"w": w},
        )
    if k > 31:
        # Two-bit packing is documented/validated up to 31-mers; beyond that
        # callers must bump the hash version deliberately.
        raise MiniseedError(
            ErrorCode.PARAMETER_CONFLICT,
            "k > 31 is not supported by hash version " + HASH_VERSION,
            context={"k": k, "hash_version": HASH_VERSION},
        )


def minimum_length(k: int, w: int) -> int:
    """Smallest sequence length that covers at least one full window."""
    return k + w - 1


@dataclass(frozen=True)
class Minimizer:
    """One selected seed position."""

    offset: int            # k-mer start offset on the indexed strand
    window_index: int      # first window index of the emitting run
    hash: int
    canonical: str
    orientation: str       # orientation of the k-mer vs its canonical form


@dataclass
class MinimizerScan:
    """Full scan result with provenance statistics for logging/auditing."""

    k: int
    w: int
    hash_version: str
    sequence_length: int
    total_windows: int
    windows_without_kmer: int          # windows fully covered by N k-mers
    minimizers: list[Minimizer] = field(default_factory=list)

    @property
    def distinct_hashes(self) -> int:
        return len({m.hash for m in self.minimizers})


def scan_minimizers(sequence: str, k: int, w: int) -> MinimizerScan:
    """Scan ``sequence`` and return its deduplicated minimizer records.

    Pure function: no I/O, no global state -- the independent oracle in the
    test suite re-derives expected results with a separate brute-force
    implementation rather than calling this function.
    """
    validate_params(k, w)
    if len(sequence) < minimum_length(k, w):
        raise MiniseedError(
            ErrorCode.SEQUENCE_TOO_SHORT,
            f"sequence length {len(sequence)} cannot cover a window "
            f"(need >= k + w - 1 = {minimum_length(k, w)})",
            context={"length": len(sequence), "k": k, "w": w},
        )

    # Valid (N-free) k-mers with their offsets and canonical forms.
    valid: list[tuple[int, CanonicalKmer]] = list(iter_kmers(sequence, k))
    total_windows = len(sequence) - k + 1 - w + 1

    # Sliding-window minimum via a monotonic deque of
    # ``(hash, offset, order, canonical)`` with non-decreasing hashes.
    # The front is always the window minimum; on EQUAL hashes the leftmost
    # (earlier) entry is retained because entries are popped only on a strictly
    # smaller hash -- this implements the fixed leftmost-offset tie-break.
    records: list[Minimizer] = []
    dq: deque[tuple[int, int, int, CanonicalKmer]] = deque()
    valid_offsets = [off for off, _ in valid]
    admit = 0  # index into ``valid`` of next k-mer to admit
    windows_without = 0
    prev_had_pick = False

    for s in range(total_windows):
        hi = s + w - 1  # inclusive rightmost k-mer offset of window s
        while admit < len(valid) and valid_offsets[admit] <= hi:
            off, ck = valid[admit]
            hv = kmer_hash(ck.canonical)
            while dq and dq[-1][0] > hv:
                dq.pop()
            dq.append((hv, off, admit, ck))
            admit += 1
        # Expire k-mers that fell out of the window's left edge.
        while dq and dq[0][1] < s:
            dq.popleft()

        if not dq:
            # No N-free k-mer in this window: adjacency is broken, so if the
            # same value reappears later it starts a fresh run/record.
            windows_without += 1
            prev_had_pick = False
            continue

        hv, off, _order, ck = dq[0]
        # First-occurrence dedup by VALUE over ADJACENT windows: emit only
        # when this window's minimizer hash differs from the previous valid
        # window's pick (or adjacency was broken by an N-only window).
        if not records or not prev_had_pick or records[-1].hash != hv:
            records.append(
                Minimizer(
                    offset=off,
                    window_index=s,
                    hash=hv,
                    canonical=ck.canonical,
                    orientation=ck.orientation,
                )
            )
        prev_had_pick = True

    return MinimizerScan(
        k=k,
        w=w,
        hash_version=HASH_VERSION,
        sequence_length=len(sequence),
        total_windows=total_windows,
        windows_without_kmer=windows_without,
        minimizers=records,
    )
