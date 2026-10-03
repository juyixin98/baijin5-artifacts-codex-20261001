"""Immutable, fully-specified MFCC configuration.

Every algorithmic choice that could make "normal input look right while
edge input is silently wrong" is pinned down here and validated up front:

- pre-emphasis coefficient and boundary rule (x[-1] = 0)
- frame length / hop in milliseconds, converted with round()
- window: symmetric Hamming (fixed, not configurable)
- FFT size: exactly frame_length (no implicit zero-padding)
- mel scale: HTK (2595 * log10(1 + f/700)), triangular filters, no
  Slaney-style area normalisation
- log floor applied to mel energies before the logarithm (natural log)
- DCT: type-II, orthonormal, first n_mfcc coefficients
- delta regression width (frames each side), edge-replication boundary
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .errors import ConfigError

#: Window is part of the fixed spec; symmetric (not periodic) Hamming.
WINDOW_TYPE = "hamming-symmetric"


@dataclass(frozen=True)
class MFCCConfig:
    sample_rate: int = 16000
    frame_length_ms: float = 25.0
    hop_length_ms: float = 10.0
    preemphasis_coef: float = 0.97
    n_mels: int = 26
    n_mfcc: int = 13
    fmin_hz: float = 20.0
    fmax_hz: float | None = None  # None -> sample_rate / 2
    log_floor: float = 1e-10
    delta_width: int = 2  # N: frames used on each side of the regression

    # -- derived quantities -------------------------------------------------
    @property
    def frame_length(self) -> int:
        return int(round(self.frame_length_ms * self.sample_rate / 1000.0))

    @property
    def hop_length(self) -> int:
        return int(round(self.hop_length_ms * self.sample_rate / 1000.0))

    @property
    def nfft(self) -> int:
        # Fixed spec: FFT size equals the frame length, no zero padding.
        return self.frame_length

    @property
    def n_freq_bins(self) -> int:
        return self.nfft // 2 + 1

    @property
    def fmax(self) -> float:
        return float(self.fmax_hz) if self.fmax_hz is not None else self.sample_rate / 2.0

    # -- validation -----------------------------------------------------------
    def validate(self) -> "MFCCConfig":
        problems: list[str] = []
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int):
            problems.append("sample_rate must be an integer")
        elif self.sample_rate <= 0:
            problems.append("sample_rate must be positive")
        if not (self.frame_length_ms > 0):
            problems.append("frame_length_ms must be positive")
        if not (self.hop_length_ms > 0):
            problems.append("hop_length_ms must be positive")
        if self.frame_length < 2:
            problems.append(
                f"frame too short: {self.frame_length} sample(s) from "
                f"{self.frame_length_ms} ms at {self.sample_rate} Hz"
            )
        if self.hop_length < 1:
            problems.append(
                f"hop too short: {self.hop_length} sample(s) from "
                f"{self.hop_length_ms} ms at {self.sample_rate} Hz"
            )
        if self.hop_length > self.frame_length:
            problems.append(
                f"hop_length ({self.hop_length}) must not exceed "
                f"frame_length ({self.frame_length})"
            )
        if not (0.0 <= self.preemphasis_coef < 1.0):
            problems.append("preemphasis_coef must be in [0, 1)")
        if self.n_mels < 1:
            problems.append("n_mels must be >= 1")
        if not (1 <= self.n_mfcc <= self.n_mels):
            problems.append(f"n_mfcc ({self.n_mfcc}) must be in [1, n_mels={self.n_mels}]")
        if not (0.0 <= self.fmin_hz < self.fmax):
            problems.append(f"need 0 <= fmin_hz ({self.fmin_hz}) < fmax ({self.fmax})")
        if self.fmax > self.sample_rate / 2.0 + 1e-9:
            problems.append(
                f"fmax ({self.fmax}) must not exceed Nyquist ({self.sample_rate / 2.0})"
            )
        if not (self.log_floor > 0.0):
            problems.append("log_floor must be positive")
        if isinstance(self.delta_width, bool) or self.delta_width < 1:
            problems.append("delta_width must be an integer >= 1")
        if problems:
            raise ConfigError(
                "invalid MFCC configuration: " + "; ".join(problems),
                detail={"problems": problems},
            )
        return self

    def with_overrides(self, **overrides) -> "MFCCConfig":
        """Return a validated copy with the given fields replaced."""
        return replace(self, **{k: v for k, v in overrides.items() if v is not None}).validate()

    def as_dict(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "frame_length_ms": self.frame_length_ms,
            "hop_length_ms": self.hop_length_ms,
            "preemphasis_coef": self.preemphasis_coef,
            "n_mels": self.n_mels,
            "n_mfcc": self.n_mfcc,
            "fmin_hz": self.fmin_hz,
            "fmax_hz": self.fmax,
            "log_floor": self.log_floor,
            "delta_width": self.delta_width,
            "window": WINDOW_TYPE,
            "frame_length_samples": self.frame_length,
            "hop_length_samples": self.hop_length,
            "nfft": self.nfft,
        }
