"""Differential tests against the independent stdlib oracle.

The oracle (tests/reference/independent_oracle.py) re-derives LORD 3
directly from the paper and never imports the app package.  This test also
asserts that source-level independence, so the two cannot silently share a
mistake through an import.
"""

import pathlib

import numpy as np

from app.contracts import LORD3State, step
from reference.independent_oracle import oracle_decisions

ORACLE_PATH = (
    pathlib.Path(__file__).resolve().parent
    / "reference"
    / "independent_oracle.py"
)


def test_oracle_does_not_import_app():
    text = ORACLE_PATH.read_text(encoding="utf-8")
    assert "from app" not in text
    assert "import app" not in text


def _kernel_rows(p_values):
    state = LORD3State.initial()
    rows = []
    for i, p in enumerate(p_values, start=1):
        state, d = step(state, f"H{i}", p)
        rows.append(d)
    return rows


def _assert_matches(p_values, rel=1e-15, abs_=1e-18):
    expected = oracle_decisions(list(p_values))
    actual = _kernel_rows(p_values)
    for e, a in zip(expected, actual):
        assert a.gamma_value == e["gamma"]
        assert abs(a.threshold - e["threshold"]) <= abs_ + rel * abs(
            e["threshold"]
        )
        assert a.rejected is e["rejected"]
        assert abs(a.wealth_before - e["wealth_before"]) <= abs_ + rel * abs(
            e["wealth_before"]
        )
        assert abs(a.wealth_after - e["wealth_after"]) <= abs_ + rel * abs(
            e["wealth_after"]
        )
        assert a.tau == e["tau_after"]
        assert abs(a.w_tau_used - e["anchor_wealth_used"]) <= abs_


def test_matches_official_sample():
    _assert_matches([1e-7, 0.1, 0.00025, 0.07])


def test_matches_all_null_random_streams_across_seeds():
    for seed in (1, 2, 3, 42, 777, 20260929):
        rng = np.random.default_rng(seed)
        ps = rng.random(400).tolist()
        _assert_matches(ps)


def test_matches_mixed_tiny_pvalue_streams():
    rng = np.random.default_rng(99)
    # Interleave near-zero p-values (frequent rejections) with uniform ones.
    ps = []
    for _ in range(300):
        ps.append(10 ** rng.uniform(-12, 0))
        ps.append(float(rng.random()))
    _assert_matches(ps)


def test_matches_boundary_grids():
    g1_w0 = 0.011638205782941741 * 0.005
    # p-values straddling the first two dynamic thresholds.
    ps = [
        0.0, g1_w0, g1_w0 * (1 + 1e-12), g1_w0 * (1 - 1e-12),
        1.0, 5e-324,
    ]
    _assert_matches(ps)


def test_matches_adversarial_long_zero_then_signal():
    ps = [0.999] * 50 + [1e-12] * 5 + [0.8] * 50 + [1e-9]
    _assert_matches(ps)
