"""Independent numerical validation package (NumPy oracle + checks)."""

from . import fixtures, oracle
from .checks import CheckResult, run_all_checks
from .verifier import (
    StepResult,
    VerificationReport,
    run_scenario,
)

__all__ = [
    "CheckResult", "StepResult", "VerificationReport",
    "fixtures", "oracle", "run_all_checks", "run_scenario",
]
