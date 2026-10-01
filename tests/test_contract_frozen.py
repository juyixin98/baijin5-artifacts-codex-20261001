"""The frozen contract itself: constants, constraints, fingerprint.

Reference literals here come from the paper (arXiv:1603.09000 Eq. 31) and
from scripts/print_reference_values.py, computed with the independent
stdlib-only oracle — never from the app package under test.
"""

import math

import pytest

from app.contracts import (
    ALPHA,
    B0,
    CONTRACT_FINGERPRINT,
    GAMMA_C,
    W0,
    contract_manifest,
    gamma_m,
)


def test_frozen_constants_match_paper():
    assert ALPHA == 0.05
    assert W0 == 0.005
    assert B0 == 0.045
    assert GAMMA_C == 0.07720838
    # Paper feasibility conditions for LORD 3.
    assert 0.0 <= W0 <= ALPHA
    assert B0 > 0.0
    assert W0 + B0 <= ALPHA
    assert W0 + B0 == pytest.approx(ALPHA)


def test_gamma_first_six_literals():
    expected = {
        1: 0.011638205782941741,
        2: 0.011638205782941741,  # log(max(m,2)) guard: gamma1 == gamma2
        3: 0.009912498794460713,
        4: 0.008243606058967332,
        5: 0.006988869709347985,
        6: 0.006045900341664855,
    }
    for m, value in expected.items():
        assert gamma_m(m) == pytest.approx(value, rel=0.0, abs=1e-18)


def test_gamma_is_nonincreasing_and_summable():
    seq = [gamma_m(m) for m in range(1, 5001)]
    assert all(g >= 0.0 for g in seq)
    assert all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
    # Partial sum to 1000 is ~0.258; the infinite sum (scripted tail-integral
    # check) is ~0.935, i.e. the paper feasibility bound sum gamma <= 1.
    assert sum(gamma_m(m) for m in range(1, 1001)) < 0.27
    with pytest.raises(ValueError):
        gamma_m(0)


def test_first_threshold_is_gamma1_times_w0():
    assert gamma_m(1) * W0 == pytest.approx(
        5.819102891470871e-05, rel=0.0, abs=1e-20
    )


def test_contract_fingerprint_is_stable():
    # Any change to a frozen constant must visibly break this literal.
    assert CONTRACT_FINGERPRINT == (
        "d126245803f7c3885df0c1e5652ad79ccea2983c45d5618a85619225308f3cb1"
    )


def test_manifest_declares_online_assumptions_and_rejects_bh_confusion():
    manifest = contract_manifest()
    assert manifest["rule"] == "LORD3"
    assert manifest["reward_timing"] == "same_step_paper_eq9_15"
    assert manifest["reject_on"] == "p <= alpha (closed interval)"
    assert "arXiv:1603.09000" in manifest["paper"]
    joined = " ".join(manifest["assumptions"]).lower()
    assert "independent" in joined
    assert "fixed pre-committed" in joined
    assert "online" in joined
    assert "offline bh is not a substitute" in joined
    # The onlineFDR reward-timing discrepancy must be disclosed, not hidden.
    assert "onlinefdr" in manifest["onlinefdr_note"].lower()
    assert math.isfinite(float(manifest["gamma_C"]))
