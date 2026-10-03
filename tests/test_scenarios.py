"""Scenario tests on synthetic fixtures: where LMS/NLMS works, and where it
cannot. Evaluation is always against the known clean signal / known plant.

Coefficient convention: the filter stores weights in buffer order
(oldest -> newest), so the known plant (scipy.signal.lfilter convention)
compares as its time reversal, ``plant[::-1]``.
"""

from __future__ import annotations

import numpy as np

from app.algorithms.lms import AdaptiveFilter, FilterSpec
from app.algorithms.metrics import (
    coefficient_error_db,
    noise_reduction_db,
    residual_mse,
)
from app.config import Settings
from app.fixtures.synth import (
    abrupt_change_scenario,
    correlated_noise_scenario,
    decorrelated_reference_scenario,
)


def run(spec: FilterSpec, reference, desired, freeze_mask=None):
    f = AdaptiveFilter(spec, Settings())
    result = f.process_block(reference, desired, freeze_mask=freeze_mask)
    return f, result


class TestApplicableCases:
    def test_nlms_identifies_correlated_noise(self):
        sc = correlated_noise_scenario(n=16000, filter_length=64, seed=7)
        spec = FilterSpec("nlms", 64, mu=0.1, epsilon=1e-8)
        f, result = run(spec, sc.reference, sc.desired)
        nr = noise_reduction_db(sc.desired, result.errors, sc.clean)
        ce = coefficient_error_db(f.weights(), sc.plants[0][::-1])
        assert nr > 12.0, f"expected >12 dB noise reduction, got {nr:.2f}"
        assert ce < -12.0, f"expected coefficient error <-12 dB, got {ce:.2f}"
        # Residual approaches the clean tone.
        assert residual_mse(result.errors[-2000:], sc.clean[-2000:]) < 0.02

    def test_lms_converges_with_small_step(self):
        sc = correlated_noise_scenario(n=8000, filter_length=32, seed=8)
        spec = FilterSpec("lms", 32, mu=0.02, epsilon=1e-8)
        f, result = run(spec, sc.reference, sc.desired)
        nr = noise_reduction_db(sc.desired, result.errors, sc.clean)
        assert nr > 10.0, f"expected >10 dB noise reduction, got {nr:.2f}"

    def test_abrupt_plant_change_reconverges(self):
        sc = abrupt_change_scenario(n=8000, filter_length=64, seed=11)
        spec = FilterSpec("nlms", 64, mu=0.2, epsilon=1e-8)
        f, result = run(spec, sc.reference, sc.desired)
        change = sc.meta["change_index"]
        tail = slice(change + 2000, None)  # well after the switch
        nr_after = noise_reduction_db(
            sc.desired[tail], result.errors[tail], sc.clean[tail]
        )
        assert nr_after > 15.0, (
            f"expected reconvergence after plant change, got {nr_after:.2f} dB"
        )
        # Final coefficients align with the *second* plant, not the first.
        assert coefficient_error_db(f.weights(), sc.plants[1][::-1]) < -15.0
        assert coefficient_error_db(f.weights(), sc.plants[0][::-1]) > -3.0


class TestFailureCases:
    def test_decorrelated_reference_cannot_cancel_noise(self):
        """With an independent reference, no linear filter of x can remove
        the noise. The evaluation must report ~0 dB, i.e. honestly fail —
        output energy dropping would NOT count as success."""
        sc = decorrelated_reference_scenario(n=4000, filter_length=64, seed=21)
        spec = FilterSpec("nlms", 64, mu=0.5, epsilon=1e-8)
        _, result = run(spec, sc.reference, sc.desired)
        nr = noise_reduction_db(sc.desired, result.errors, sc.clean)
        assert abs(nr) < 3.0, (
            f"decorrelated reference should yield ~0 dB, got {nr:.2f} dB"
        )

    def test_output_energy_reduction_is_not_success(self):
        """A filter frozen at zero weights has minimal output energy yet
        achieves zero noise reduction — the metric must say so."""
        sc = correlated_noise_scenario(n=1000, filter_length=16, seed=5)
        spec = FilterSpec("nlms", 16, mu=0.5, epsilon=1e-8)
        _, result = run(
            spec, sc.reference, sc.desired,
            freeze_mask=np.ones(1000, dtype=bool),
        )
        assert np.all(result.outputs == 0.0)  # zero output energy
        nr = noise_reduction_db(sc.desired, result.errors, sc.clean)
        assert abs(nr) < 1e-9  # ...and exactly zero noise reduction
