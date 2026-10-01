"""Paired randomized-experiment inference service.

Modules:
    config    - environment-driven configuration and dependency pins
    contracts - statistical contract types (pair design, p-values, sets)
    design    - paired randomization set construction (strict pairing)
    estimator - exact and approximate randomization-test kernel + inversion
    evidence  - diagnostics, error taxonomy, request-scoped evidence logs
    api       - FastAPI application
    storage   - SQLite persistence for requests and evidence
"""

__version__ = "1.0.0"
