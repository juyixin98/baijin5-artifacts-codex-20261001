"""Fixed algorithm parameters for the WSOLA backend.

These values are part of the documented contract: the analysis/synthesis
hop relationship, the local search radius and the overlap window rule are
FIXED and must not be tuned per request. Changing them changes the
observable per-segment offsets, so they live in one frozen dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WsolaConfig:
    """Fixed WSOLA parameters.

    window_length:   overlap-add window length L (samples). Must be 2 * synthesis_hop
                     so the periodic Hann window satisfies constant-overlap-add (COLA)
                     exactly: w[n] + w[n + Hs] == 1 for all interior n.
    synthesis_hop:   Hs, output samples produced per segment.
    search_radius:   D, local match search is delta in [-D, +D] samples around the
                     nominal analysis position.
    corr_epsilon:    below this normalized-correlation denominator the match is
                     declared degenerate (silence / DC): every candidate scores 0.0
                     and the deterministic tie-break applies.
    envelope_epsilon: OLA window-sum below which output is defined as 0 (only
                     reachable at the extreme edges).
    """

    window_length: int = 1024
    synthesis_hop: int = 512
    search_radius: int = 256
    corr_epsilon: float = 1e-12
    envelope_epsilon: float = 1e-8

    def __post_init__(self) -> None:
        if self.window_length != 2 * self.synthesis_hop:
            raise ValueError(
                "window_length must equal 2 * synthesis_hop (50% overlap, COLA)"
            )
        if self.synthesis_hop <= 0 or self.search_radius < 0:
            raise ValueError("synthesis_hop must be > 0 and search_radius >= 0")


#: Rate semantics: output_length = round(input_length / rate).
#: rate > 1  -> faster playback, shorter output.
#: rate < 1  -> slower playback, longer output.
#:
#: HARD limits: requests outside are rejected (422, RATE_OUT_OF_RANGE).
#: QUALITY range: inside hard limits but outside quality range the request is
#: accepted with a RATE_OUTSIDE_QUALITY_RANGE warning diagnostic; distortion
#: is not bounded there and artifacts are expected.
HARD_MIN_RATE = 0.25
HARD_MAX_RATE = 4.0
QUALITY_MIN_RATE = 0.5
QUALITY_MAX_RATE = 2.0

DEFAULT_CONFIG = WsolaConfig()
