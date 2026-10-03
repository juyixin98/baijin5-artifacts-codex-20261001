"""Offline WSOLA time-stretch core (whole-signal, pure NumPy/SciPy).

Fixed rules (contract, do not tune per request):

* Window: periodic Hann, L = 2 * Hs -> constant overlap-add (COLA) holds.
* Analysis hop Ha = rate * Hs (float). The nominal position of segment k is
  round(k * Ha), so rounding drift never exceeds 0.5 sample and never
  accumulates (explicit per-segment compensation).
* Local match: normalized cross-correlation between the already synthesized
  overlap y[o_k : o_k + Hs] and each candidate x[a + d : a + d + Hs],
  d in [-D, +D], clamped to keep the full window inside the input. Both
  sides carry the same overlap-window weight w[Hs : L], so an exact
  continuation scores exactly 1.0.
* Deterministic tie-break: candidates are scanned in the fixed order
  0, -1, +1, -2, +2, ... and a candidate only wins on a STRICTLY greater
  score. Silence/DC makes the correlation degenerate (score 0.0 for every
  candidate), so delta = 0 wins whenever it is in range. Periodic inputs
  with several equal optima resolve to the first optimum in scan order.
* Segment 0 has no overlap: delta = 0, correlation = None.
* Tail pinning: when the nominal position runs past the last placeable
  frame start (only possible for strong slow-down, where the ideal
  analysis trajectory ends beyond n - L), the search window is shifted to
  the last D-wide range of placeable frame starts (flagged pinned=True).
  The tail is therefore phase-aligned content, never zero padding.
  Because every frame is always placed, the synthesized stream always
  covers the target length.
* End handling: the number of frames covers at least the target length,
  so the final output is exactly round(n / rate) samples by trimming the
  tail (end_rule "trim", or "exact" when no trim is needed). The applied
  rule and compensation length are reported in StretchResult.

No artifact-free guarantee: WSOLA is a waveform-similarity method; outside
the documented quality rate range, or on non-stationary/noisy content,
audible artifacts are expected.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import windows

from .config import DEFAULT_CONFIG, WsolaConfig


class InputTooShortError(ValueError):
    """Raised when the input cannot hold even one analysis window."""

    def __init__(self, n: int, minimum: int) -> None:
        self.n = n
        self.minimum = minimum
        super().__init__(f"input length {n} is below minimum {minimum}")


@dataclass(frozen=True)
class SegmentDecision:
    index: int
    output_position: int
    ideal_position: float
    nominal_position: int
    delta: int
    analysis_position: int
    correlation: float | None
    degenerate: bool
    pinned: bool = False


@dataclass
class StretchResult:
    samples: np.ndarray
    target_length: int
    segments: list[SegmentDecision]
    frames_planned: int
    frames_placed: int
    end_rule: str  # "exact" | "trim"
    end_compensation_samples: int  # <= 0; |value| samples trimmed
    max_position_drift: float


def delta_scan_order(radius: int):
    """Fixed candidate order: 0, -1, +1, -2, +2, ... (ties keep the first)."""
    yield 0
    for magnitude in range(1, radius + 1):
        yield -magnitude
        yield magnitude


def normalized_correlation(
    a: np.ndarray, b: np.ndarray, eps: float
) -> tuple[float, bool]:
    """Return (score, degenerate). Degenerate -> score 0.0 by definition."""
    energy_a = float(np.dot(a, a))
    energy_b = float(np.dot(b, b))
    denominator = math.sqrt(energy_a * energy_b)
    if denominator < eps:
        return 0.0, True
    return float(np.dot(a, b) / denominator), False


def target_length_for(n: int, rate: float) -> int:
    """The exact output length contract: round(n / rate)."""
    return int(round(n / rate))


def _search_delta(
    y_overlap: np.ndarray,
    x: np.ndarray,
    nominal: int,
    lo: int,
    hi: int,
    cfg: WsolaConfig,
    overlap_weight: np.ndarray,
) -> tuple[int, float, bool]:
    """Pick the match offset in [lo, hi] under the fixed rules.

    Both sides of the match carry the same overlap-window weight, so an
    exact continuation (delta = 0 at identity rate) scores exactly 1.0.
    """
    best_delta: int | None = None
    best_score = -math.inf
    best_degenerate = False
    for delta in delta_scan_order(cfg.search_radius + max(0, -hi)):
        if delta < lo or delta > hi:
            continue
        start = nominal + delta
        candidate = (
            x[start : start + cfg.synthesis_hop] * overlap_weight
        )
        score, degenerate = normalized_correlation(
            y_overlap, candidate, cfg.corr_epsilon
        )
        if score > best_score:  # strict: first optimum in scan order wins ties
            best_delta, best_score, best_degenerate = delta, score, degenerate
    assert best_delta is not None  # lo <= hi guarantees at least one candidate
    return best_delta, best_score, best_degenerate


def search_range_for(
    nominal: int, n: int, cfg: WsolaConfig
) -> tuple[int, int, bool]:
    """Clamped search range [lo, hi] for one segment, plus the pinned flag.

    Normal frames: delta in [-D, +D] clamped so the full window stays
    inside the input. Tail frames (nominal beyond the last placeable
    start): the range shifts to the last D-wide window of placeable
    starts and the segment is flagged pinned.
    """
    D = cfg.search_radius
    lo = max(-D, -nominal)
    hi = min(D, n - cfg.window_length - nominal)
    if lo <= hi:
        return lo, hi, False
    hi = n - cfg.window_length - nominal  # < -D: tail frame
    lo = max(hi - D, -nominal)  # keep analysis_position >= 0
    return lo, hi, True


def wsola_stretch(
    x: np.ndarray, rate: float, cfg: WsolaConfig = DEFAULT_CONFIG
) -> StretchResult:
    """Stretch a mono signal to exactly round(len(x) / rate) samples."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1:
        raise ValueError("expected a mono 1-D signal")
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError(f"rate must be a positive finite number, got {rate!r}")
    n = x.shape[0]
    if n < cfg.window_length:
        raise InputTooShortError(n, cfg.window_length)

    Hs, L = cfg.synthesis_hop, cfg.window_length
    target = target_length_for(n, rate)
    analysis_hop = rate * Hs
    window = windows.hann(L, sym=False)  # periodic Hann: COLA at 50% overlap
    overlap_weight = window[Hs:]  # match weight on the overlap half

    frames_planned = max(1, math.ceil((target - L) / Hs) + 1)
    out_capacity = (frames_planned - 1) * Hs + L
    y = np.zeros(out_capacity)
    envelope = np.zeros(out_capacity)

    segments: list[SegmentDecision] = []
    max_drift = 0.0
    placed = 0
    for k in range(frames_planned):
        out_pos = k * Hs
        ideal = k * analysis_hop
        nominal = int(round(ideal))
        max_drift = max(max_drift, abs(nominal - ideal))
        if k == 0:
            delta, correlation, degenerate, pinned = 0, None, False, False
        else:
            lo, hi, pinned = search_range_for(nominal, n, cfg)
            delta, correlation, degenerate = _search_delta(
                y[out_pos : out_pos + Hs], x, nominal, lo, hi, cfg,
                overlap_weight,
            )
        analysis_pos = nominal + delta
        y[out_pos : out_pos + L] += window * x[analysis_pos : analysis_pos + L]
        envelope[out_pos : out_pos + L] += window
        segments.append(
            SegmentDecision(
                index=k,
                output_position=out_pos,
                ideal_position=ideal,
                nominal_position=nominal,
                delta=delta,
                analysis_position=analysis_pos,
                correlation=correlation,
                degenerate=degenerate,
                pinned=pinned,
            )
        )
        placed += 1

    valid_length = (placed - 1) * Hs + L
    y = y[:valid_length]
    envelope = envelope[:valid_length]
    out = np.zeros_like(y)
    np.divide(y, envelope, out=out, where=envelope > cfg.envelope_epsilon)

    # Every frame is always placed (tail pinning), so the stream always
    # covers the target: the end rule can only be "trim" or "exact".
    assert valid_length >= target
    end_rule = "exact" if valid_length == target else "trim"
    compensation = target - valid_length  # <= 0
    out = out[:target]

    return StretchResult(
        samples=out,
        target_length=target,
        segments=segments,
        frames_planned=frames_planned,
        frames_placed=placed,
        end_rule=end_rule,
        end_compensation_samples=compensation,
        max_position_drift=max_drift,
    )
