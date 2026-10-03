"""Frozen MFCC pipeline configuration.

Every algorithmic constant of the pipeline lives here so that "same config"
unambiguously means "same numbers".  The defaults are the fixed specification
of this service:

- pre-emphasis:  y[n] = x[n] - preemphasis * x[n-1],  y[0] = x[0]
- framing:       25 ms window / 10 ms hop, symmetric Hamming window
- spectrum:      rfft of length n_fft, power = |X|^2 / n_fft
- Mel scale:     HTK,  mel(f) = 2595 * log10(1 + f / 700)
- filterbank:    triangular, n_mels filters, no area normalization
- log:           natural log of max(mel_energy, log_floor)
- DCT:           DCT-II, norm="ortho", first n_mfcc coefficients
- lifter:        off by default (lifter=0); sine lifter when > 0
- delta:         width delta_width, edge-replication boundary extension
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .errors import ConfigError


@dataclass(frozen=True)
class MFCCConfig:
    sample_rate: int = 16000
    preemphasis: float = 0.97
    window_ms: float = 25.0
    hop_ms: float = 10.0
    n_fft: int = 512
    n_mels: int = 26
    n_mfcc: int = 13
    fmin: float = 20.0
    fmax: float | None = None  # None resolves to sample_rate / 2
    log_floor: float = 1e-10
    delta_width: int = 2
    lifter: int = 0

    # -- derived quantities -------------------------------------------------

    @property
    def frame_length(self) -> int:
        return int(round(self.window_ms * self.sample_rate / 1000.0))

    @property
    def hop_length(self) -> int:
        return int(round(self.hop_ms * self.sample_rate / 1000.0))

    @property
    def resolved_fmax(self) -> float:
        return self.sample_rate / 2.0 if self.fmax is None else float(self.fmax)

    @property
    def n_freq_bins(self) -> int:
        return self.n_fft // 2 + 1

    # -- validation ----------------------------------------------------------

    def validate(self) -> "MFCCConfig":
        """Raise ConfigError listing *all* violated constraints."""
        problems: list[str] = []
        if not isinstance(self.sample_rate, int) or self.sample_rate <= 0:
            problems.append(f"sample_rate must be a positive int, got {self.sample_rate!r}")
        if not (0.0 <= self.preemphasis < 1.0):
            problems.append(f"preemphasis must be in [0, 1), got {self.preemphasis}")
        if self.window_ms <= 0:
            problems.append(f"window_ms must be > 0, got {self.window_ms}")
        if self.hop_ms <= 0:
            problems.append(f"hop_ms must be > 0, got {self.hop_ms}")
        if self.sample_rate > 0:
            if self.frame_length < 2:
                problems.append(
                    f"frame_length={self.frame_length} too short "
                    f"(window_ms={self.window_ms} @ {self.sample_rate} Hz)"
                )
            if self.hop_length < 1:
                problems.append(
                    f"hop_length={self.hop_length} too short "
                    f"(hop_ms={self.hop_ms} @ {self.sample_rate} Hz)"
                )
            if self.hop_length > self.frame_length:
                problems.append(
                    f"hop_length={self.hop_length} exceeds frame_length={self.frame_length}"
                )
        if self.n_fft < self.frame_length:
            problems.append(f"n_fft={self.n_fft} < frame_length={self.frame_length}")
        if self.n_mels < 1:
            problems.append(f"n_mels must be >= 1, got {self.n_mels}")
        if not (1 <= self.n_mfcc <= self.n_mels):
            problems.append(f"n_mfcc={self.n_mfcc} must be in [1, n_mels={self.n_mels}]")
        if self.fmin < 0:
            problems.append(f"fmin must be >= 0, got {self.fmin}")
        if self.sample_rate > 0:
            fmax = self.resolved_fmax
            if fmax > self.sample_rate / 2.0 + 1e-9:
                problems.append(
                    f"fmax={fmax} exceeds Nyquist={self.sample_rate / 2.0}"
                )
            if not self.fmin < fmax:
                problems.append(f"fmin={self.fmin} must be < fmax={fmax}")
        if self.log_floor <= 0:
            problems.append(f"log_floor must be > 0, got {self.log_floor}")
        if self.delta_width < 1:
            problems.append(f"delta_width must be >= 1, got {self.delta_width}")
        if self.lifter < 0:
            problems.append(f"lifter must be >= 0, got {self.lifter}")
        if problems:
            raise ConfigError(
                "invalid MFCC configuration: " + "; ".join(problems),
                details={"problems": problems},
            )
        return self

    def with_overrides(self, **kwargs) -> "MFCCConfig":
        """Return a validated copy with the given fields replaced."""
        return replace(self, **kwargs).validate()


DEFAULT_CONFIG = MFCCConfig().validate()
