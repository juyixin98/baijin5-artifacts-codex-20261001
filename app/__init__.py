"""Local linear regression discontinuity (RD) backend.

Package layout
--------------
- contract:     statistical / API contract (pydantic schemas, typed categories)
- kernels:      kernel functions
- bandwidths:   bandwidth selection (manual + IK-style rule of thumb)
- estimator:    separate local-linear fits on each side of the cutoff
- inference:    heteroskedasticity-robust SE, normal CIs, wild bootstrap
- diagnostics:  discreteness / heaping, McCrary density test, sparse edges
- datasets:     synthetic DGP fixtures with known truth (independent oracles)
- reference:    independent numerical reference paths (not reusing the core)
- store:        SQLite persistence of runs
- api:          FastAPI wiring
"""

__version__ = "1.0.0"
