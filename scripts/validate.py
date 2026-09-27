"""Finite-difference validation runner.

Runs every registered gradcheck case (minigrad gradients vs. central finite
differences of the pure-NumPy reference loss) and prints a report. Exits
non-zero unless every case is accepted; undecidable cases are reported
separately from rejected ones.
"""

from __future__ import annotations

import sys

from minigrad.diagnostics import STATUS_ACCEPTED, STATUS_REJECTED
from minigrad.finite_difference import gradcheck
from minigrad.validation_cases import get_case, list_cases


def main() -> int:
    failures = 0
    undecidable = 0
    for name in list_cases():
        report = gradcheck(get_case(name))
        worst_ratio = max(p.max_error_ratio for p in report.params)
        print(f"[{report.status:>11}] {name:<24} max_error_ratio={worst_ratio:.3g}")
        for param in report.params:
            print(f"             - {param.name}: ratio={param.max_error_ratio:.3g} "
                  f"({param.status}) {param.reason if param.status != STATUS_ACCEPTED else ''}")
        if report.status == STATUS_REJECTED:
            failures += 1
        elif report.status != STATUS_ACCEPTED:
            undecidable += 1
    print()
    print(f"{len(list_cases())} cases: "
          f"{len(list_cases()) - failures - undecidable} accepted, "
          f"{undecidable} undecidable, {failures} rejected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
