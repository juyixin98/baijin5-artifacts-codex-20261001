"""Configuration contract for the lookahead limiter.

The algorithm parameters are FIXED by this contract:

- Peak detection: per-sample maximum of |x| across all channels
  (channels are fully linked: one gain trajectory drives every channel,
  so inter-channel balance is preserved exactly).
- Required gain:  r[n] = min(1, threshold / peak[n]).
- Attack anticipation (backward exponential ramp over the lookahead window):
      g1[n] = min_{0 <= j <= L} r[n + j] * attack_coeff ** (-j)
- Release (forward exponential recovery, multiplicative):
      g[n] = min(g1[n], g[n-1] * release_coeff)
- Coefficients derive from time constants:
      attack_coeff  = exp(-1000 / (attack_ms  * sample_rate))   (< 1)
      release_coeff = exp(+1000 / (release_ms * sample_rate))   (> 1)

Threshold semantics: by default the threshold constrains the *sample peak*
of the output (|out[n]| <= threshold for every emitted sample, structurally
guaranteed because g[n] <= r[n]). With ``true_peak=True`` the detector
measures an oversampled (reconstructed) peak instead; the ceiling then holds
for the oversampled output up to a documented tolerance (see docs).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

#: Minimum lookahead (samples) required in true-peak mode so the
#: polyphase resampling filter's span fits inside the lookahead window.
MIN_LOOKAHEAD_TRUE_PEAK_SAMPLES = 32


@dataclass(frozen=True)
class LimiterConfig:
    sample_rate: float = 8000.0
    threshold_dbfs: float = -6.0
    attack_ms: float = 0.5
    release_ms: float = 50.0
    lookahead_ms: float = 2.0
    true_peak: bool = False
    oversample_factor: int = 4

    # -- derived quantities -------------------------------------------------

    @property
    def threshold_linear(self) -> float:
        return 10.0 ** (self.threshold_dbfs / 20.0)

    @property
    def lookahead_samples(self) -> int:
        return max(1, round(self.lookahead_ms * self.sample_rate / 1000.0))

    @property
    def attack_coeff(self) -> float:
        return math.exp(-1000.0 / (self.attack_ms * self.sample_rate))

    @property
    def release_coeff(self) -> float:
        return math.exp(1000.0 / (self.release_ms * self.sample_rate))

    # -- validation / (de)serialisation -------------------------------------

    def validate(self) -> "LimiterConfig":
        problems = []
        if not math.isfinite(self.sample_rate) or self.sample_rate <= 0:
            problems.append("sample_rate must be a positive finite number")
        if not math.isfinite(self.threshold_dbfs) or self.threshold_dbfs > 0:
            problems.append("threshold_dbfs must be finite and <= 0")
        if not math.isfinite(self.attack_ms) or self.attack_ms <= 0:
            problems.append("attack_ms must be a positive finite number")
        if not math.isfinite(self.release_ms) or self.release_ms <= 0:
            problems.append("release_ms must be a positive finite number")
        if not math.isfinite(self.lookahead_ms) or self.lookahead_ms <= 0:
            problems.append("lookahead_ms must be a positive finite number")
        if self.oversample_factor not in (2, 4, 8):
            problems.append("oversample_factor must be one of 2, 4, 8")
        if problems:
            raise ValueError("invalid limiter config: " + "; ".join(problems))
        if self.true_peak and self.lookahead_samples < MIN_LOOKAHEAD_TRUE_PEAK_SAMPLES:
            raise ValueError(
                "true_peak mode requires lookahead_samples >= "
                f"{MIN_LOOKAHEAD_TRUE_PEAK_SAMPLES} "
                f"(got {self.lookahead_samples}); increase lookahead_ms"
            )
        return self

    @classmethod
    def from_dict(cls, data: dict) -> "LimiterConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**data).validate()

    def to_dict(self) -> dict:
        return asdict(self)
