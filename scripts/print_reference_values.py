#!/usr/bin/env python3
"""Print frozen reference literals for the test suite.

Run once, inspect, then paste the numbers into the tests.  The trajectory
numbers come exclusively from the independent stdlib oracle
(tests/reference/independent_oracle.py); the synthetic-fixture literals come
from NumPy/seeds so they can be regenerated and compared with the frozen
copies committed in the tests.

Usage: python scripts/print_reference_values.py
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))
from reference.independent_oracle import W0, gamma, oracle_decisions  # noqa: E402


def main() -> None:
    print("== gamma m=1..6 ==")
    for m in range(1, 7):
        print(f"m={m}: {gamma(m)!r}")
    print("sum check 1..2_000_000 close to paper bound (<1):")
    print("  g1*w0 =", repr(gamma(1) * W0))

    print("\n== official onlineFDR teaching sample (1e-7,.1,.00025,.07) ==")
    for row in oracle_decisions([1e-7, 0.1, 0.00025, 0.07]):
        print(row)

    print("\n== extended hand sequence (+0.001, +0.9, +2e-5) ==")
    for row in oracle_decisions([1e-7, 0.1, 0.00025, 0.07, 0.001, 0.9, 2e-5]):
        print(row)

    print("\n== boundary: p exactly equal to first threshold ==")
    a1 = gamma(1) * W0
    print("a1 =", repr(a1), " rejected =", oracle_decisions([a1])[0]["rejected"])

    print("\n== numpy fixture: default_rng(7) first 8 uniforms ==")
    print(np.random.default_rng(7).random(8).tolist())

    print("\n== numpy fixture: default_rng(20260929) first 8 uniforms ==")
    print(np.random.default_rng(20260929).random(8).tolist())

    # Fixed-seed regression anchors for app.simulation-driven runs.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from app.lord3 import run_sequence  # noqa: E402
    from app.simulation import mixed_stream, null_stream  # noqa: E402

    s = null_stream(200, seed=101)
    r = run_sequence(s.hypothesis_ids, s.p_values)
    print("\nnull_stream(200,101): R =", r.n_rejections,
          "final_wealth =", repr(r.final_wealth),
          "first4 p =", s.p_values[:4])

    s2 = mixed_stream(200, 0.2, seed=202, beta_a=0.05)
    r2 = run_sequence(s2.hypothesis_ids, s2.p_values)
    v = sum(1 for d, null in zip(r2.decisions, s2.is_null)
            if d.rejected and null)
    print("mixed_stream(200,.2,202): R =", r2.n_rejections, "V =", v,
          "final_wealth =", repr(r2.final_wealth),
          "null_positions_first5 =",
          [i for i, x in enumerate(s2.is_null[:40]) if not x][:5])


if __name__ == "__main__":
    main()
