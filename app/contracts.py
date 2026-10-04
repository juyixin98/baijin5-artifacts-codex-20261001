"""Request/response contracts (the API's sample-level schema)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StftParamsIn(BaseModel):
    """Parameter block shared by all endpoints."""

    model_config = ConfigDict(extra="forbid")

    n_fft: int = Field(ge=1, description="FFT size")
    win_length: int = Field(ge=1, description="window length in samples")
    hop_length: int = Field(ge=1, description="hop between frame starts")
    window: Literal["hann", "hamming", "blackman", "rect"] = "hann"


class SpectrumFrame(BaseModel):
    """One complex spectrum row, split into components for JSON."""

    model_config = ConfigDict(extra="forbid")

    real: list[float]
    imag: list[float]


class StftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    samples: list[float] = Field(min_length=1)
    params: StftParamsIn


class StftResponse(BaseModel):
    request_id: str
    version: str
    n_samples: int
    n_frames: int
    n_bins: int
    hop_length: int
    #: Input-sample position each frame is centred on: k * hop_length.
    frame_centers: list[int]
    spectrogram: list[SpectrumFrame]


class IstftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spectrogram: list[SpectrumFrame] = Field(min_length=1)
    params: StftParamsIn
    n_samples: int = Field(ge=1, description="exact output length")


class IstftResponse(BaseModel):
    request_id: str
    version: str
    n_samples: int
    #: Minimum OLA normalisation denominator over the kept region.
    min_ola_denominator: float
    samples: list[float]


class ExpandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spectrogram: list[SpectrumFrame] = Field(min_length=1)
    n_fft: int = Field(ge=1)


class ExpandResponse(BaseModel):
    request_id: str
    version: str
    n_frames: int
    n_bins_full: int
    spectrogram: list[SpectrumFrame]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    request_id: str
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
