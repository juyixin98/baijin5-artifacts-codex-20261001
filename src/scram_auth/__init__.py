"""SCRAM-SHA-256 local test-only authentication package."""
from __future__ import annotations

from .config import AppConfig, load_config
from .errors import FailureCategory, ScramError

__all__ = ["AppConfig", "FailureCategory", "ScramError", "load_config", "__version__"]
__version__ = "1.0.0"
