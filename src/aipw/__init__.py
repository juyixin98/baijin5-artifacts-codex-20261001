"""AIPW estimation backend package.

Modules
-------
contract : statistical contract, configuration, typed result objects and error taxonomy
models   : propensity / outcome model implementations with train-only standardization
crossfit : fold assignment and out-of-fold prediction alignment
estimators : the AIPW / IPW / g-computation estimation kernels
influence : influence-function variance and cluster-robust independent units
jobs     : SQLite-backed run registry with explicit run states
api      : FastAPI application exposing estimation and replay endpoints
"""

__version__ = "1.0.0"
