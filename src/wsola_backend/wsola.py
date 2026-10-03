"""Core WSOLA (Waveform Similarity Overlap-Add) time-stretch algorithm.

Fixed rules (not configurable at runtime, by design):

- Analysis hop  Ha = round(sample_rate * 10 ms)
- Synthesis hop Hs = round(Ha * time_scale)          -> Ha and Hs set the target length
- Window        Hann, length L = 4 * Ha              -> input covered by 4 frames;
                within the supported range [0.5, 2.0] the output overlap
                L - Hs is always >= 50% of the window
- Local search  delta in [-Ha, +Ha] around the natural analysis position,
                scored by normalized cross-correlation over the output
                overlap region (L - Hs samples)
- Tie-break     among candidates within TIE_TOL of the best score:
                smallest |delta| first, then the smaller delta (negative wins).
                Silence (undefined correlation) scores 0 everywhere, so the
                tie-break deterministically selects delta = 0.

End/boundary semantics:

- Target length T = round(N * time_scale); the output is exactly T samples.
- Frame count K = ceil((T - L) / Hs) + 1 covers T; the tail of the last frame
  beyond T is trimmed.
- When k*Ha + search range would run past the input, the natural position is
  clamped to N - L and the search range shrinks symmetrically (end
  compensation). The final frames therefore reuse the input tail rather than
  inventing samples.
- Overlap-add is normalized by the window-sum, so gain is uniform; the very
  first output sample is 0 because the Hann window endpoint is 0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal.windows import hann

ANALYSIS_HOP_MS = 10.0
# Correlation denominators below this are treated as silence: every candidate
# scores 0 and the tie-break selects the natural position.
SILENCE_EPS = 1e-12
# Scores within this absolute tolerance of the best are considered tied.
TIE_TOL = 1e-9


@dataclass(frozen=True)
class WsolaParams:
    sample_rate: int
    time_scale: float
    analysis_hop: int
    synthesis_hop: int
    window_len: int
    search_radius: int


def make_params(sample_rate: int, time_scale: float) -> WsolaParams:
    ha = max(1, int(round(sample_rate * ANALYSIS_HOP_MS / 1000.0)))
    hs = max(1, int(round(ha * time_scale)))
    return WsolaParams(
        sample_rate=sample_rate,
        time_scale=float(time_scale),
        analysis_hop=ha,
        synthesis_hop=hs,
        window_len=4 * ha,
        search_radius=ha,
    )


def target_length(n_samples: int, time_scale: float) -> int:
    return int(round(n_samples * time_scale))


def frame_count(params: WsolaParams, target: int) -> int:
    if target <= params.window_len:
        return 1
    return math.ceil((target - params.window_len) / params.synthesis_hop) + 1


@dataclass(frozen=True)
class FrameDecision:
    index: int
    synthesis_pos: int
    natural_pos: int  # clamped k*Ha
    match_pos: int    # chosen absolute analysis position
    score: float      # best normalized correlation; 0.0 for frame 0 / silence
    tied_candidates: int  # candidates within TIE_TOL of the best score

    @property
    def offset(self) -> int:
        return self.match_pos - self.natural_pos


@dataclass
class StretchResult:
    output: np.ndarray
    target_length: int
    frames: list[FrameDecision]
    params: WsolaParams

    @property
    def offsets(self) -> list[int]:
        return [f.offset for f in self.frames]


def choose_match_position(
    ref: np.ndarray,
    x: np.ndarray,
    x_nat: int,
    lo: int,
    hi: int,
    ov_len: int,
) -> tuple[int, float, int]:
    """Pick the analysis position in [lo, hi] best matching `ref`.

    Returns (position, best_score, tied_count). Deterministic tie-break:
    among positions scoring within TIE_TOL of the best, choose the smallest
    |pos - x_nat|; break remaining ties toward the smaller position.
    """
    starts = np.arange(lo, hi + 1)
    candidates = np.lib.stride_tricks.sliding_window_view(x[lo : hi + ov_len], ov_len)
    ref_norm = float(np.linalg.norm(ref))
    if ref_norm < SILENCE_EPS:
        scores = np.zeros(len(starts))
    else:
        cand_norm = np.linalg.norm(candidates, axis=1)
        denom = cand_norm * ref_norm
        raw = candidates @ ref
        scores = np.where(denom < SILENCE_EPS, 0.0, raw / np.maximum(denom, 1e-300))
    best = float(scores.max())
    tied_mask = scores >= best - TIE_TOL
    tied_starts = starts[tied_mask]
    deltas = tied_starts - x_nat
    order = np.lexsort((deltas, np.abs(deltas)))
    chosen = int(tied_starts[order[0]])
    return chosen, best, int(tied_mask.sum())


def compute_frame(
    x: np.ndarray,
    out: np.ndarray,
    wsum: np.ndarray,
    window: np.ndarray,
    params: WsolaParams,
    k: int,
) -> FrameDecision:
    """Compute and overlap-add frame k. Shared by batch and streaming paths."""
    n = x.shape[0]
    p = params
    y = k * p.synthesis_hop
    x_nat = min(k * p.analysis_hop, n - p.window_len)
    lo = max(0, x_nat - p.search_radius)
    hi = min(n - p.window_len, x_nat + p.search_radius)
    if k == 0:
        # No synthesized reference exists yet; natural position by definition.
        pos, score, tied = x_nat, 0.0, 1
    else:
        ov = p.window_len - p.synthesis_hop  # actual output overlap region
        ref = out[y : y + ov]
        pos, score, tied = choose_match_position(ref, x, x_nat, lo, hi, ov)
    out[y : y + p.window_len] += window * x[pos : pos + p.window_len]
    wsum[y : y + p.window_len] += window
    return FrameDecision(
        index=k,
        synthesis_pos=y,
        natural_pos=x_nat,
        match_pos=pos,
        score=score,
        tied_candidates=tied,
    )


def normalize_region(out: np.ndarray, wsum: np.ndarray, start: int, stop: int) -> np.ndarray:
    """Return the normalized (window-sum-corrected) copy of out[start:stop]."""
    seg = out[start:stop].copy()
    w = wsum[start:stop]
    nz = w > SILENCE_EPS
    seg[nz] /= w[nz]
    return seg


def wsola_stretch(samples: np.ndarray, params: WsolaParams) -> StretchResult:
    """Batch WSOLA. Input is mono float; output has exactly target_length samples."""
    x = np.ascontiguousarray(samples, dtype=np.float64)
    n = x.shape[0]
    p = params
    target = target_length(n, p.time_scale)
    k_total = frame_count(p, target)
    out = np.zeros((k_total - 1) * p.synthesis_hop + p.window_len)
    wsum = np.zeros_like(out)
    window = hann(p.window_len, sym=False)
    frames = [compute_frame(x, out, wsum, window, p, k) for k in range(k_total)]
    normalized = normalize_region(out, wsum, 0, out.shape[0])
    if normalized.shape[0] >= target:
        normalized = normalized[:target]
    else:  # unreachable given frame_count, kept as an explicit guard
        normalized = np.pad(normalized, (0, target - normalized.shape[0]))
    return StretchResult(output=normalized, target_length=target, frames=frames, params=p)
