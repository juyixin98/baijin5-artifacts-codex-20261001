"""Hard-seed handling: big-M sufficiency, conflicts, overflow guard."""

from __future__ import annotations

import numpy as np
import pytest

from graphcut.errors import ResourceExhaustedError, StateConflictError
from graphcut.models import EnergySpec, PairwiseTerm, SeedSet
from graphcut.seeds import apply_seeds, sufficient_seed_weight


def spec_with_seeds(fg=(), bg=(), unary_scale=1.0) -> EnergySpec:
    return EnergySpec(
        width=2,
        height=2,
        unary0=np.full((2, 2), 1.0 * unary_scale),
        unary1=np.full((2, 2), 2.0 * unary_scale),
        pairwise=(PairwiseTerm(0, 1, 0.0, 3.0, 1.0, 0.0),),
        seeds=SeedSet(foreground=tuple(fg), background=tuple(bg)),
    )


class TestBigM:
    def test_exact_value(self):
        # unary bound: 4 pixels * max(1, 2) = 8; pairwise max = 3; M = 12
        spec = spec_with_seeds()
        assert sufficient_seed_weight(spec) == pytest.approx(12.0)

    def test_applied_to_correct_side(self):
        spec = spec_with_seeds(fg=(0,), bg=(3,))
        u0, u1, big_m = apply_seeds(spec)
        # foreground seed on pixel 0 forbids label 0 there
        assert u0.reshape(-1)[0] == pytest.approx(1.0 + big_m)
        assert u1.reshape(-1)[0] == pytest.approx(2.0)
        # background seed on pixel 3 forbids label 1 there
        assert u1.reshape(-1)[3] == pytest.approx(2.0 + big_m)
        assert u0.reshape(-1)[3] == pytest.approx(1.0)
        # untouched pixel unchanged
        assert u0.reshape(-1)[1] == pytest.approx(1.0)
        assert u1.reshape(-1)[1] == pytest.approx(2.0)

    def test_inputs_not_mutated(self):
        spec = spec_with_seeds(fg=(0,))
        before = spec.unary0.copy()
        apply_seeds(spec)
        np.testing.assert_array_equal(spec.unary0, before)


class TestSeedConflicts:
    def test_conflicting_seeds_rejected(self):
        spec = spec_with_seeds(fg=(1, 2), bg=(2, 3))
        with pytest.raises(StateConflictError) as exc:
            apply_seeds(spec)
        assert exc.value.code == "conflicting_seeds"
        assert exc.value.category.value == "state_conflict"
        assert exc.value.details["pixels"] == [2]


class TestOverflowGuard:
    def test_huge_costs_rejected_as_resource_exhaustion(self):
        spec = spec_with_seeds(fg=(0,), unary_scale=1e300)
        with pytest.raises(ResourceExhaustedError) as exc:
            apply_seeds(spec)
        assert exc.value.code == "seed_weight_overflow"
        assert exc.value.category.value == "resource_exhausted"
