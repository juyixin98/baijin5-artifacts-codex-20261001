"""Local-linear Regression Discontinuity backend.

Package layout
--------------
- ``app.core``      : statistical contract (kernels, WLS engine, bandwidth,
                      local-linear RD estimator, diagnostics).
- ``app.dgp``       : synthetic data fixtures.
- ``app.storage``   : SQLite persistence of runs.
- ``app.api``       : FastAPI service.
"""

__version__ = "1.0.0"
