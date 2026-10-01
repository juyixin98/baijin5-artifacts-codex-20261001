"""Statistical core: kernels, WLS, bandwidth, estimator, diagnostics.

Modules
-------
kernels      : compact-support kernel functions (reproducible by name)
wls          : weighted least squares engine with robust standard errors
bandwidth    : ROT and plugin (Imbens-Kalyanaraman style) selectors
estimator    : side-specific local-linear RD fit (the estimand contract)
diagnostics  : discrete running variable, heaping, McCrary density test
contracts    : typed result objects shared with the API layer
"""

__all__ = [
    "kernels",
    "wls",
    "bandwidth",
    "estimator",
    "diagnostics",
    "contracts",
]
