"""Convergence evaluation against the known clean signal and true plant.

The fixture's internal consistency is cross-checked with
``scipy.signal.lfilter`` — an independent implementation path from the
fixture's ``np.convolve`` — so the ground truth is not generated solely
by the code under test.
"""

from __future__ import annotations

import logging

import numpy as np
import scipy.signal

from app.dsp.fixtures import TRUE_PLANT, correlated_noise_scenario
from app.dsp.lms import AdaptiveFilter
from app.dsp.metrics import coefficient_error_norm, mse, snr_improvement_db

logger = logging.getLogger("opp506.tests")


def test_fixture_consistent_with_scipy_reference() -> None:
    scenario = correlated_noise_scenario(512, seed=11)
    # Independent check: primary - clean must equal the true plant applied
    # to the reference, computed here with scipy instead of np.convolve.
    expected_noise = scipy.signal.lfilter(TRUE_PLANT, [1.0], scenario.reference)
    np.testing.assert_allclose(
        scenario.primary - scenario.clean, expected_noise, rtol=1e-10, atol=1e-12
    )


def test_nlms_converges_to_true_plant() -> None:
    # mu=0.02: the steady-state coefficient error is a stationary noise
    # floor (driven by the clean signal leaking into the update), measured
    # at ~0.07 mean / ~0.24 worst-case instantaneous across seeds, so the
    # coefficient assertion uses the tail *average*, not a single sample.
    n, seed = 8000, 1
    scenario = correlated_noise_scenario(n, seed)
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.02)

    first = flt.process_block(scenario.reference[: n // 2], scenario.primary[: n // 2])
    tail_errs = []
    error_tail = []
    for k in range(n // 2, n, 200):
        block = flt.process_block(
            scenario.reference[k : k + 200], scenario.primary[k : k + 200]
        )
        tail_errs.append(coefficient_error_norm(flt.weights, scenario.true_coeffs))
        error_tail.append(block.error)
    full_error = np.concatenate([first.error, *error_tail])

    snr_gain = snr_improvement_db(scenario.primary, full_error, scenario.clean)
    coeff_floor = float(np.mean(tail_errs))
    residual_tail = mse(full_error[n // 2 :], scenario.clean[n // 2 :])
    clean_power = mse(scenario.clean, np.zeros(n))

    logger.info(
        "convergence run: scenario=correlated_noise seed=%d n=%d "
        "snr_improvement_db=%.2f coeff_floor_mean=%.5f residual_tail_mse=%.6f "
        "rationale=metrics vs known clean signal and true plant; coeff floor "
        "averaged over tail because steady-state weight error is stationary noise",
        seed, n, snr_gain, coeff_floor, residual_tail,
    )
    assert snr_gain > 15.0, "NLMS should remove most of the correlated noise"
    assert coeff_floor < 0.12, "weights should settle near the true plant"
    assert residual_tail < 0.1 * clean_power


def test_lms_converges_with_stable_step() -> None:
    n, seed = 12000, 2
    scenario = correlated_noise_scenario(n, seed)
    flt = AdaptiveFilter(filter_len=4, algorithm="lms", mu=0.05)
    result = flt.process_block(scenario.reference, scenario.primary)

    snr_gain = snr_improvement_db(scenario.primary, result.error, scenario.clean)
    logger.info(
        "convergence run: algorithm=lms mu=0.05 snr_improvement_db=%.2f", snr_gain
    )
    assert snr_gain > 15.0


def test_coefficient_error_drops_from_transient_to_floor() -> None:
    # The coefficient error does not decrease monotonically: after the
    # initial transient it fluctuates around a stationary floor (clean
    # signal acts as weight noise). The robust assertion is therefore
    # transient-vs-floor: error right after start must be several times
    # larger than the averaged tail floor.
    n, seed = 8000, 5
    scenario = correlated_noise_scenario(n, seed)
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.02)

    flt.process_block(scenario.reference[:50], scenario.primary[:50])
    err_transient = coefficient_error_norm(flt.weights, scenario.true_coeffs)

    tail_errs = []
    for k in range(50, n, 200):
        flt.process_block(scenario.reference[k : k + 200], scenario.primary[k : k + 200])
        if k >= n // 2:
            tail_errs.append(coefficient_error_norm(flt.weights, scenario.true_coeffs))
    err_floor = float(np.mean(tail_errs))

    logger.info(
        "coefficient trajectory: err@50=%.4f tail_floor_mean=%.4f "
        "rationale=floor is stationary weight noise, not monotone decay",
        err_transient, err_floor,
    )
    assert err_floor < 0.25 * err_transient
    assert err_floor < 0.12


def test_evaluation_rejects_output_energy_criterion() -> None:
    # Guard the evaluation contract: a filter that zeroes its output has
    # minimal output energy, yet here it scores only ~6 dB because the
    # metric compares against the clean signal (erasing the desired signal
    # is penalized). A genuinely converged filter must beat the
    # zero-output "cheat" by a wide margin.
    scenario = correlated_noise_scenario(4000, seed=9)
    zero_gain = snr_improvement_db(
        scenario.primary, np.zeros(4000), scenario.clean
    )
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.05)
    result = flt.process_block(scenario.reference, scenario.primary)
    converged_gain = snr_improvement_db(scenario.primary, result.error, scenario.clean)

    logger.info(
        "energy-criterion guard: zero_output_gain=%.2f converged_gain=%.2f "
        "rationale=quiet output is not denoising success",
        zero_gain, converged_gain,
    )
    assert converged_gain > zero_gain + 5.0
