"""MFCC + delta/delta-delta backend with no external model dependencies."""

from .config import DEFAULT_CONFIG, MFCCConfig
from .delta import compute_delta, compute_delta_delta
from .errors import (
    ConfigError,
    EmptyFilterError,
    InputContractError,
    MFCCError,
    StreamStateError,
)
from .pipeline import PipelineResult, extract_features, log_mel_spectrogram
from .streaming import StreamingMFCC, StreamEmit

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_CONFIG",
    "MFCCConfig",
    "PipelineResult",
    "StreamEmit",
    "StreamingMFCC",
    "compute_delta",
    "compute_delta_delta",
    "extract_features",
    "log_mel_spectrogram",
    "MFCCError",
    "ConfigError",
    "EmptyFilterError",
    "InputContractError",
    "StreamStateError",
    "__version__",
]
