"""Fixed limiter configuration — the algorithm contract.

The following decisions are FIXED by contract version
``lookahead-limiter/v1`` and must not be silently changed:

- Peak detection range: per-frame scalar peak = max over channels of |x|
  (full-band, no weighting filter).
- Peak mode: SAMPLE peak. The threshold constrains per-sample peaks only,
  NOT the reconstructed inter-sample (true) peak. True-peak limiting would
  require oversampling the detection path and is explicitly out of scope;
  ``peak_mode != "sample"`` is rejected.
- Channel linking: fully linked. One scalar gain, derived from the loudest
  channel, is applied to every channel (stereo image is preserved).
- Required gain: r = min(1, threshold / peak).
- Lookahead: the gain applied to output frame j is the attack/release
  smoothed value of R(j) = min(r(j), r(j+1), ..., r(j+L)) where L is the
  lookahead in frames. The smoother therefore sees a peak's required gain
  L+1 frames before that frame leaves the delay line.
- Smoothing: one-pole, g = c*g_prev + (1-c)*R with
  c = attack_coeff when R < g_prev else release_coeff,
  coeff = exp(-1 / (tau_seconds * sample_rate)).
- Latency: exactly L frames. flush() emits the final L frames still in the
  delay line, so total emitted frames == total input frames (no tail loss).

Ceiling promise (derived in README): for input peak A > threshold T,
    output_peak <= T + (A - T) * attack_coeff ** (L + 1)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

ALGORITHM_CONTRACT_VERSION = "lookahead-limiter/v1"
PEAK_MODE_SAMPLE = "sample"


@dataclass(frozen=True)
class LimiterConfig:
    sample_rate: int = 48000
    channels: int = 2
    threshold: float = 0.5  # linear sample-peak ceiling target
    lookahead_ms: float = 5.0
    attack_ms: float = 0.5
    release_ms: float = 50.0
    peak_mode: str = PEAK_MODE_SAMPLE  # fixed; see module docstring

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")
        if self.channels < 1:
            raise ValueError("channels must be >= 1")
        if not 0.0 < self.threshold:
            raise ValueError("threshold must be > 0")
        if self.lookahead_ms <= 0:
            raise ValueError("lookahead_ms must be > 0")
        if self.attack_ms <= 0:
            raise ValueError("attack_ms must be > 0")
        if self.release_ms <= 0:
            raise ValueError("release_ms must be > 0")
        if self.peak_mode != PEAK_MODE_SAMPLE:
            raise ValueError(
                f"unsupported peak_mode {self.peak_mode!r}: this build limits "
                "sample peaks only (true-peak detection is not implemented)"
            )

    @property
    def lookahead_samples(self) -> int:
        return int(round(self.lookahead_ms * 1e-3 * self.sample_rate))

    @property
    def attack_coeff(self) -> float:
        return math.exp(-1.0 / (self.attack_ms * 1e-3 * self.sample_rate))

    @property
    def release_coeff(self) -> float:
        return math.exp(-1.0 / (self.release_ms * 1e-3 * self.sample_rate))

    def promised_ceiling(self, input_peak: float) -> float:
        """Analytic upper bound on the output sample peak."""
        excess = max(0.0, input_peak - self.threshold)
        return self.threshold + excess * self.attack_coeff ** (self.lookahead_samples + 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lookahead_samples"] = self.lookahead_samples
        d["attack_coeff"] = self.attack_coeff
        d["release_coeff"] = self.release_coeff
        d["algorithm_version"] = ALGORITHM_CONTRACT_VERSION
        return d
