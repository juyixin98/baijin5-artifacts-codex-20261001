"""Core cascade correctness: impulse/step response, channel isolation,
near-unit-circle poles, overflow handling, initial conditions."""

import numpy as np
import pytest
import scipy.signal

from app.dsp.cascade import InitialCondition, SosCascadeFilter, TransientStrategy
from app.dsp.coefficients import normalize_and_validate
from app.errors import NonFiniteOutputError, SampleBlockError
from tests.reference import dc_gain, reference_sos_filter

LIMITS = dict(max_sections=32, pole_radius_limit=1.0)


def butter_sos(order=4, cutoff=0.2, btype="low"):
    return normalize_and_validate(
        scipy.signal.butter(order, cutoff, btype=btype, output="sos"), **LIMITS
    ).sections


def test_impulse_response_matches_scipy_and_python_reference(runlog):
    sos = butter_sos()
    n = 256
    x = np.zeros((n, 1))
    x[0, 0] = 1.0

    got = SosCascadeFilter(sos, 1).process_block(x)
    ref_py = reference_sos_filter(sos, x)
    ref_scipy = scipy.signal.sosfilt(sos, x[:, 0]).reshape(-1, 1)

    err_py = float(np.max(np.abs(got - ref_py)))
    err_scipy = float(np.max(np.abs(got - ref_scipy)))
    runlog("impulse", n=n, n_sections=sos.shape[0],
           max_abs_err_vs_python_ref=err_py,
           max_abs_err_vs_scipy=err_scipy,
           tolerance=1e-12,
           rationale="transposed DF-II must match DF-I and scipy.sosfilt")
    assert err_py < 1e-12
    assert err_scipy < 1e-12


def test_step_response_settles_to_analytic_dc_gain(runlog):
    sos = butter_sos()
    n = 4000
    x = np.ones((n, 1))
    got = SosCascadeFilter(sos, 1).process_block(x)
    expected = dc_gain(sos)
    tail = float(got[-1, 0])
    runlog("step", n=n, analytic_dc_gain=expected, settled_output=tail,
           tolerance=1e-9,
           rationale="unit step steady state equals prod(sum b / sum a)")
    assert tail == pytest.approx(expected, abs=1e-9)


def test_dc_steady_initial_condition_starts_settled(runlog):
    sos = butter_sos()
    x = np.ones((512, 1))
    got = SosCascadeFilter(sos, 1, InitialCondition.DC_STEADY).process_block(x)
    expected = dc_gain(sos)
    max_dev = float(np.max(np.abs(got - expected)))
    runlog("dc_steady", analytic_dc_gain=expected, max_deviation=max_dev,
           tolerance=1e-9,
           rationale="pre-charged state must remove the step transient")
    assert max_dev < 1e-9


def test_multichannel_state_isolation(runlog, rng):
    sos = butter_sos()
    n = 300
    drive = rng.standard_normal((n, 1))
    two_ch = np.concatenate([drive, np.zeros((n, 1))], axis=1)

    got = SosCascadeFilter(sos, 2).process_block(two_ch)
    solo = SosCascadeFilter(sos, 1).process_block(drive)

    cross_talk = float(np.max(np.abs(got[:, 1])))
    mismatch = float(np.max(np.abs(got[:, [0]] - solo)))
    runlog("isolation", n=n, channels=2, cross_talk=cross_talk,
           ch0_vs_single_channel=mismatch,
           rationale="silent channel must stay exactly silent; driven "
                     "channel must match a single-channel run bitwise")
    assert cross_talk == 0.0
    assert mismatch == 0.0


def test_poles_near_unit_circle_match_reference(runlog):
    # resonator with poles at r = 0.9999: long memory, stress for state
    r, theta = 0.9999, 0.3
    sos = np.array([[1.0, 0.0, 0.0, 1.0, -2 * r * np.cos(theta), r * r]])
    n = 5000
    x = np.zeros((n, 1))
    x[0, 0] = 1.0

    got = SosCascadeFilter(sos, 1).process_block(x)
    ref = reference_sos_filter(sos, x)
    err = float(np.max(np.abs(got - ref)))
    runlog("near_unit_circle", pole_radius=r, n=n, max_abs_err=err,
           tolerance=1e-9, output_finite=bool(np.all(np.isfinite(got))),
           rationale="r=0.9999 impulse response must track DF-I reference")
    assert np.all(np.isfinite(got))
    assert err < 1e-9


def test_nonfinite_input_rejected_as_input_error():
    sos = butter_sos()
    x = np.zeros((16, 1))
    x[3, 0] = np.inf
    with pytest.raises(SampleBlockError):
        SosCascadeFilter(sos, 1).process_block(x)


def test_overflow_raises_and_state_not_committed(runlog):
    # gain-10 FIR section: 1e308 * 10 overflows to inf on the first sample
    sos = np.array([[10.0, 0.0, 0.0, 1.0, 0.0, 0.0]])
    filt = SosCascadeFilter(sos, 1)
    before = filt.state_snapshot()
    x = np.full((64, 1), 1e308)
    with np.errstate(over="ignore", invalid="ignore"):
        with pytest.raises(NonFiniteOutputError) as exc:
            filt.process_block(x)
    after = filt.state_snapshot()
    runlog("overflow", category=exc.value.category.value,
           state_committed=not np.array_equal(before, after),
           rationale="non-finite output must raise computation_failed and "
                     "leave stream state untouched (never zeroed)")
    assert exc.value.category.value == "computation_failed"
    assert np.array_equal(before, after)


def test_wrong_channel_count_rejected():
    sos = butter_sos()
    with pytest.raises(SampleBlockError):
        SosCascadeFilter(sos, 2).process_block(np.zeros((8, 3)))


def test_coefficient_switch_reset_matches_fresh_filter(runlog, rng):
    sos_a = butter_sos(order=2, cutoff=0.1)
    sos_b = butter_sos(order=4, cutoff=0.3)
    filt = SosCascadeFilter(sos_a, 1)
    filt.process_block(rng.standard_normal((128, 1)))  # dirty the state

    filt.replace_coefficients(sos_b, TransientStrategy.RESET_STATE)
    x = rng.standard_normal((256, 1))
    got = filt.process_block(x)
    fresh = SosCascadeFilter(sos_b, 1).process_block(x)
    runlog("switch_reset", max_abs_err=float(np.max(np.abs(got - fresh))),
           rationale="reset_state switch must behave as a fresh filter")
    assert np.array_equal(got, fresh)


def test_coefficient_switch_preserve_state_matches_reference(runlog, rng):
    sos_a = butter_sos(order=2, cutoff=0.1)
    sos_b = butter_sos(order=2, cutoff=0.3)
    filt = SosCascadeFilter(sos_a, 1)
    filt.process_block(rng.standard_normal((128, 1)))

    zi_before = filt.state_snapshot()
    filt.replace_coefficients(sos_b, TransientStrategy.PRESERVE_STATE)
    zi_after = filt.state_snapshot()
    runlog("switch_preserve",
           state_preserved=bool(np.array_equal(zi_before, zi_after)),
           rationale="preserve_state must carry z across the switch; "
                     "the resulting transient is documented behavior")
    assert np.array_equal(zi_before, zi_after)
