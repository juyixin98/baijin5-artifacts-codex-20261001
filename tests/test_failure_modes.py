"""Failure-mode and boundary scenarios: decorrelated reference, silence,
abrupt plant change. Each test asserts the concrete expected outcome and
names the failure category, not just that the code runs.
"""

from __future__ import annotations

import logging

import numpy as np

from app.dsp.fixtures import (
    abrupt_change_scenario,
    decorrelated_reference_scenario,
    silence_scenario,
)
from app.dsp.lms import AdaptiveFilter
from app.dsp.metrics import mse, snr_improvement_db

logger = logging.getLogger("opp506.tests")


def test_decorrelated_reference_fails_visibly() -> None:
    """Failure category: reference_not_correlated.

    With a statistically independent reference there is nothing the filter
    can cancel; SNR improvement must stay near/below 0 dB. This is the
    documented applicability boundary of the algorithm.
    """
    n, seed = 4000, 21
    scenario = decorrelated_reference_scenario(n, seed)
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.5)
    result = flt.process_block(scenario.reference, scenario.primary)

    snr_gain = snr_improvement_db(scenario.primary, result.error, scenario.clean)
    logger.info(
        "failure-mode run: scenario=decorrelated_reference snr_improvement_db=%.2f "
        "category=reference_not_correlated rationale=independent reference carries "
        "no information about primary noise",
        snr_gain,
    )
    assert abs(snr_gain) < 3.0, "decorrelated reference must not yield real cancellation"
    # And the residual must not be mistaken for success by energy alone:
    assert mse(result.error, scenario.clean) > 0.5 * mse(
        scenario.primary, scenario.clean
    )


def test_silence_window_freezes_progress_numerically() -> None:
    """During a silent reference the weights cannot move (update is exactly
    zero) and the denominator floor is eps — no NaN, no drift."""
    n, seed = 3000, 31
    scenario = silence_scenario(n, seed, silence=(1000, 2000))
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.5)

    flt.process_block(scenario.reference[:1000], scenario.primary[:1000])
    # The first filter_len silent samples still flush old samples out of
    # the tap buffer; freeze guarantee applies once the buffer is zero.
    flt.process_block(scenario.reference[1000:1008], scenario.primary[1000:1008])
    before_silence = flt.weights.copy()

    silent = flt.process_block(scenario.reference[1008:2000], scenario.primary[1008:2000])
    np.testing.assert_array_equal(flt.weights, before_silence)
    assert silent.min_denominator == flt.eps

    flt.process_block(scenario.reference[2000:], scenario.primary[2000:])
    # After silence the filter resumes adapting from the same point.
    assert flt.samples_processed == n
    logger.info(
        "silence run: weights frozen across [1000,2000), min_denominator=%.2e",
        silent.min_denominator,
    )


def test_abrupt_change_reconverges() -> None:
    """After the plant switches, residual error spikes then re-converges."""
    n, seed, change = 8000, 41, 4000
    scenario = abrupt_change_scenario(n, seed, change_at=change)
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.1)
    result = flt.process_block(scenario.reference, scenario.primary)

    # The spike is measured over the first 100 post-change samples (the
    # filter reconverges within a few hundred, so a long window would
    # average the spike away); before/settled use 500-sample windows.
    before = mse(result.error[change - 500 : change], scenario.clean[change - 500 : change])
    just_after = mse(result.error[change : change + 100], scenario.clean[change : change + 100])
    settled = mse(result.error[-500:], scenario.clean[-500:])

    logger.info(
        "abrupt-change run: change_at=%d mse_before=%.5f mse_just_after=%.5f "
        "mse_settled=%.5f rationale=error must spike at change then recover",
        change, before, just_after, settled,
    )
    assert just_after > 4.0 * before, "plant change must cause a visible error spike"
    assert settled < 0.25 * just_after, "filter must reconverge after the change"


def test_frozen_interval_blocks_adaptation_to_change() -> None:
    """If adaptation is frozen exactly over the plant change, the filter
    keeps the OLD plant and the settled error stays high — demonstrating
    the frozen-adaptation contract end to end."""
    n, seed, change = 8000, 43, 4000
    scenario = abrupt_change_scenario(n, seed, change_at=change)
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.1)

    freeze = np.zeros(n, dtype=bool)
    freeze[change:] = True  # never adapt after the change
    result = flt.process_block(scenario.reference, scenario.primary, freeze)

    settled = mse(result.error[-500:], scenario.clean[-500:])
    before = mse(result.error[change - 500 : change], scenario.clean[change - 500 : change])
    logger.info(
        "frozen-after-change run: mse_before=%.5f mse_settled=%.5f "
        "rationale=frozen filter cannot track the new plant",
        before, settled,
    )
    assert result.frozen_samples == n - change
    assert settled > 4.0 * before, "frozen filter must fail on the new plant"
