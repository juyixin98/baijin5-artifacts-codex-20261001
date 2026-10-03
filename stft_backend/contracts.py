"""Request / response data contracts (pydantic v2).

Complex spectrum bins are transported as ``[real, imag]`` pairs, which are
unambiguous across JSON clients. Every response shares the same envelope so
a request id, processing metadata and a separated error block are always
present.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# A complex number as a JSON [real, imag] pair.
ComplexPair = tuple[float, float]

WindowInput = str | list[float]


class StftRequest(BaseModel):
    signal: list[float] = Field(..., description="Real-valued input samples.")
    nperseg: int | None = Field(None, ge=2, description="Window length (even or odd).")
    hop: int | None = Field(None, ge=1, description="Analysis/synthesis hop.")
    nfft: int | None = Field(None, ge=2, description="FFT length; >= nperseg.")
    window: WindowInput | None = Field(None, description="Window name or samples.")
    onesided: bool = Field(True, description="Return the one-sided spectrum.")


class IstftRequest(BaseModel):
    # spectrum[frame][bin] = [real, imag]
    spectrum: list[list[ComplexPair]]
    nperseg: int = Field(..., ge=2)
    hop: int = Field(..., ge=1)
    nfft: int | None = Field(None, ge=2)
    window: WindowInput | None = None
    onesided: bool = True
    signal_length: int | None = Field(None, ge=1)


class RoundtripRequest(StftRequest):
    signal_length: int | None = Field(
        None, ge=1, description="Override trimming length on the inverse path."
    )


class ValidateRequest(BaseModel):
    nperseg: int = Field(..., ge=2)
    hop: int = Field(..., ge=1)
    nfft: int | None = Field(None, ge=2)
    window: WindowInput | None = None


class StreamCreateRequest(BaseModel):
    direction: Literal["analyze", "synthesize"]
    nperseg: int | None = Field(None, ge=2)
    hop: int | None = Field(None, ge=1)
    nfft: int | None = Field(None, ge=2)
    window: WindowInput | None = None
    onesided: bool = True


class AnalyzeChunkRequest(BaseModel):
    samples: list[float] = Field(default_factory=list)
    finish: bool = False


class SynthesizeFrameRequest(BaseModel):
    frame_index: int = Field(..., ge=0)
    bins: list[ComplexPair]
    finish: bool = False
    signal_length: int | None = Field(None, ge=1)


class ErrorBlock(BaseModel):
    code: str
    message: str
    stage: str
    details: dict[str, Any] = Field(default_factory=dict)


class ResponseEnvelope(BaseModel):
    success: bool
    request_id: str
    data: dict[str, Any] | None = None
    error: ErrorBlock | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
