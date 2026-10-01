"""Paired randomized-experiment inference service.

Modules
-------
app.core.contract  - statistical contract (types, validation, test statistics)
app.core.kernel    - exact randomization test, grid inversion, Monte-Carlo engine
app.core.intervals - interval-set algebra (exact / approximate)
app.config         - configuration (env driven)
app.storage        - SQLite persistence of requests and runs
app.diagnostics    - request-scoped structured logging
app.evidence       - independent enumeration evidence + diagnostic summaries
app.repro          - synthetic fixtures and reproducibility replay
app.api            - FastAPI wiring
"""

__version__ = "1.0.0"
