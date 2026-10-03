"""Request/response contracts for the MFCC service.

The sample contract, in one place:

- ``samples`` is a mono, float64 PCM sequence in [-1, 1]-ish range (values
  are not clipped, but NaN/inf are rejected), at ``sample_rate`` Hz.
- A request may override any MFCCConfig field; the merged config is
  validated and echoed back in the response so results are reproducible.
- Responses carry a ``run_id`` so any test log or client log line can be
  tied to the exact computation that produced it.
"""

from __future__ import annotations

import platform
import uuid

import numpy as np
import scipy
from pydantic import BaseModel, Field

from .config import DEFAULT_CONFIG, MFCCConfig

SERVICE_VERSION = "0.1.0"

# Requests larger than 5 minutes of audio at the request's own sample rate
# are rejected as contract violations, not silently truncated.
MAX_DURATION_SECONDS = 300.0


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def versions() -> dict[str, str]:
    return {
        "service": SERVICE_VERSION,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


class ConfigOverrides(BaseModel):
    """Optional per-request overrides; omitted fields keep service defaults."""

    preemphasis: float | None = None
    window_ms: float | None = None
    hop_ms: float | None = None
    n_fft: int | None = None
    n_mels: int | None = None
    n_mfcc: int | None = None
    fmin: float | None = None
    fmax: float | None = None
    log_floor: float | None = None
    delta_width: int | None = None
    lifter: int | None = None

    def apply(self, base: MFCCConfig = DEFAULT_CONFIG) -> MFCCConfig:
        overrides = {k: v for k, v in self.model_dump().items() if v is not None}
        return base.with_overrides(**overrides)


class MFCCRequest(BaseModel):
    samples: list[float] = Field(min_length=1)
    sample_rate: int = 16000
    config: ConfigOverrides = Field(default_factory=ConfigOverrides)


class FeatureBlock(BaseModel):
    n_frames: int
    n_coeffs: int
    values: list[list[float]]


class MFCCResponse(BaseModel):
    run_id: str
    n_samples: int
    n_frames: int
    mfcc: FeatureBlock
    delta: FeatureBlock
    delta_delta: FeatureBlock
    warnings: list[str]
    config: dict
    versions: dict[str, str]


class StreamCreateRequest(BaseModel):
    sample_rate: int = 16000
    config: ConfigOverrides = Field(default_factory=ConfigOverrides)


class StreamCreateResponse(BaseModel):
    session_id: str
    config: dict
    versions: dict[str, str]


class StreamChunkRequest(BaseModel):
    samples: list[float] = Field(min_length=1)


class StreamEmitResponse(BaseModel):
    session_id: str
    start_frame: int
    n_frames: int
    mfcc: FeatureBlock
    delta: FeatureBlock
    delta_delta: FeatureBlock
    frames_emitted_total: int
    samples_seen_total: int
    finalized: bool


class ErrorBody(BaseModel):
    run_id: str
    category: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
