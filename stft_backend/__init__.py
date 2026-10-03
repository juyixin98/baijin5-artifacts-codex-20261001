"""STFT / ISTFT backend package.

Modules:
    errors:          error codes and the domain exception hierarchy
    config:          environment-driven settings and service metadata
    logging_setup:   structured logging with request-id correlation
    windows:         window function resolution
    numeric:         reconstruction-condition and numerical checks
    algorithms:      batch STFT / ISTFT core (pure NumPy)
    streaming:       frame-wise streaming analysis / OLA synthesis state
    contracts:       request/response data contracts (pydantic)
    api:             FastAPI application
"""

from .config import SERVICE_VERSION

__version__ = SERVICE_VERSION
__all__ = ["__version__"]
