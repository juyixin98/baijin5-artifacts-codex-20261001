"""Request/response schemas — the sample contract of the service.

Requests are validated twice: structurally here (pydantic), and
numerically in the pipeline (finite, 1-D, long enough). Errors never
collapse into a generic 200.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .config import MFCCConfig

MAX_SAMPLES = 5_000_000

# Non-finite values are admitted through schema validation on purpose so the
# pipeline's own numeric boundary check (InvalidAudioError, 400) stays the
# single authority — and so FastAPI never tries to serialise a NaN inside a
# validation-error response.
SampleList = Annotated[
    list[Annotated[float, Field(allow_inf_nan=True)]],
    Field(min_length=1, max_length=MAX_SAMPLES),
]


class ConfigOverrides(BaseModel):
    """Optional per-request overrides; omitted fields keep the defaults."""

    model_config = ConfigDict(extra="forbid")

    frame_length_ms: float | None = None
    hop_length_ms: float | None = None
    preemphasis_coef: float | None = None
    n_mels: int | None = None
    n_mfcc: int | None = None
    fmin_hz: float | None = None
    fmax_hz: float | None = None
    log_floor: float | None = None
    delta_width: int | None = None

    def apply(self, base: MFCCConfig) -> MFCCConfig:
        return base.with_overrides(**self.model_dump())


class FeaturesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_rate: int = Field(default=16000, gt=0)
    samples: SampleList
    config: ConfigOverrides | None = None
    include_intermediates: bool = False

    def to_config(self) -> MFCCConfig:
        base = MFCCConfig(sample_rate=self.sample_rate)
        if self.config is not None:
            base = self.config.apply(base)
        return base.validate()


class StreamStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_rate: int = Field(default=16000, gt=0)
    config: ConfigOverrides | None = None

    def to_config(self) -> MFCCConfig:
        base = MFCCConfig(sample_rate=self.sample_rate)
        if self.config is not None:
            base = self.config.apply(base)
        return base.validate()


class StreamChunkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    samples: SampleList
