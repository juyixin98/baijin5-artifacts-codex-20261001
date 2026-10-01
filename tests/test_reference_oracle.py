"""Cross-implementation checks.

This file contains a THIRD, fully self-contained LORD++ implementation written
only with the Python standard library. It imports neither the production kernel
nor the simulation reference loop, so agreement cannot be circular. It then
checks:

1. the self-contained oracle against hard-coded hand literals;
2. the production kernel (app.statistics) against the oracle on seeded streams;
3. the simulation reference (app.simulation) against the oracle.
"""

from __future__ import annotations

import math

import numpy as np

from app.simulation import gaussian_stream, null_stream, run_lordpp_reference
from app.statistics import LordConfig, LordState

C = 0.0722
ALPHA = 0.05
W0 = 0.045
H = 1000


def _oracle(pvals, alpha=ALPHA, w0=W0, horizon=H):
    """Self-contained canonical LORD++: stdlib math only, local formula copy.

    Net reward per rejection is (b - alpha_tau) with the SPENT level alpha_tau
    (a pre-p-value quantity), not the observed p-value.
    """
    b = alpha - w0
    total = math.fsum(C * math.log(max(i, 2)) / max(i, 2) for i in range(1, horizon + 1))

    def g(j):
        return (C * math.log(max(j, 2)) / max(j, 2)) / total

    tau, spent = [], []
    out = []
    for t in range(1, len(pvals) + 1):
        w = w0 + math.fsum(g(t - k) * (b - a) for k, a in zip(tau, spent))
        thr = g(t) * w
        p = float(pvals[t - 1])
        rej = p <= thr
        out.append((w, thr, rej))
        if rej:
            tau.append(t)
            spent.append(thr)
    return out


def test_self_contained_oracle_matches_hand_literals():
    literals = [
        (0.045, 0.0006461705090967012, True),
        (0.045062518138192384, 0.000647068228589669, False),
        (0.045062518138192384, 0.0006837192518151271, True),
        (0.045128038232621195, 0.0006480090542068497, False),
        (0.045124497101047024, 0.0006018049450165025, True),
    ]
    for (w, thr, rej), (ew, ethr, erej) in zip(
        _oracle([0.0005, 0.5, 0.0005, 0.9, 0.0004]), literals
    ):
        assert w == pytest_appx(ew)
        assert thr == pytest_appx(ethr)
        assert rej is erej


def pytest_appx(v, rel=1e-12, abs_=1e-14):
    class _A:
        def __eq__(self, other):
            return math.isclose(other, v, rel_tol=rel, abs_tol=abs_)

    return _A()


def test_kernel_agrees_with_oracle_on_mixed_streams():
    for seed in range(1, 31):
        stream = gaussian_stream(150, 0.3, effect=3.5, seed=seed)
        ps = stream.p_values.tolist()
        kernel = LordState.initialize(LordConfig.create(alpha=ALPHA, w0=W0, horizon=H))
        for p, (_, _, oracle_rej) in zip(ps, _oracle(ps)):
            step = kernel.step(p)
            assert step.rejected is oracle_rej


def test_simulation_reference_agrees_with_oracle_on_null_streams():
    for seed in (20260927, 20260928, 42, 7, 99):
        stream = null_stream(120, seed=seed)
        ps = stream.p_values.tolist()
        ref = run_lordpp_reference(ps, alpha=ALPHA, w0=W0, horizon=H)
        oracle = _oracle(ps)
        assert ref["rejected"] == [r for _, _, r in oracle]
        for a, (_, b, _) in zip(ref["thresholds"], oracle):
            assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-15)
        for a, (b, _, _) in zip(ref["wealth"], oracle):
            assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-14)


def test_oracle_rewards_make_wealth_nonnegative():
    # Over many random streams wealth W_t must never be negative (a LORD++
    # sanity property the kernel also asserts).
    rng = np.random.default_rng(31337)
    for _ in range(20):
        ps = rng.random(100).tolist()
        for w, _, _ in _oracle(ps):
            assert w >= 0.0
