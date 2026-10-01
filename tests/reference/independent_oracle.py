"""Independent LORD 3 oracle written straight from the paper.

This module deliberately imports NOTHING from ``app``.  It is an independent
re-implementation of arXiv:1603.09000 Eq. 9/15/18/31 used as a test oracle.
If both implementations agree over many random streams, a shared slip in
either one is less likely.  The hard-coded literals asserted by the
hand-computed tests were produced by ``scripts/print_reference_values.py``
using only this module plus Python's stdlib ``math``.
"""

from __future__ import annotations

import math

ALPHA = 0.05
W0 = 0.005
B0 = 0.045
C = 0.07720838


def gamma(m: int) -> float:
    """Paper Eq. 31; gamma_1 == gamma_2 via the max(m, 2) log guard."""
    x = max(m, 2)
    return C * math.log(x) / (x * math.exp(math.sqrt(math.log(x))))


def oracle_decisions(p_values):
    """Independent paper-formula walk; paper-faithful same-step reward.

    Tracks the anchor (tau, W(tau)) explicitly: the threshold at time i uses
    the wealth recorded immediately after the most recent rejection.
    """
    tau = 0
    wealth = W0
    anchor_wealth = W0  # W(tau); equals w0 while tau == 0
    out = []
    for i, p in enumerate(p_values, start=1):
        anchor_index = tau
        anchor_value = anchor_wealth
        g = gamma(i - tau)
        threshold = g * anchor_wealth
        reject = p <= threshold
        wealth_before = wealth
        wealth = wealth_before - threshold + (B0 if reject else 0.0)
        if reject:
            tau = i
            anchor_wealth = wealth
        out.append(
            {
                "index": i,
                "p_value": p,
                "gamma": g,
                "threshold": threshold,
                "rejected": reject,
                "wealth_before": wealth_before,
                "wealth_after": wealth,
                "tau_after": tau,
                "anchor_index_used": anchor_index,
                "anchor_wealth_used": anchor_value,
            }
        )
    return out
