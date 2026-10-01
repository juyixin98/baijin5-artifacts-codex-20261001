"""Local synthetic fixtures.

All fixtures are small, hand-designed datasets with documented reference
answers (see ``reference`` fields). Reference answers are derived independently
of :mod:`app.core.kernel`: hand counting where trivial, and
:mod:`app.evidence` (plain-Python ``itertools`` enumeration) elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    differences: tuple[float, ...]
    # Independent reference information; tests assert on these exact values.
    reference: dict


FIXTURES: dict[str, Fixture] = {
    "identical_outcomes": Fixture(
        name="identical_outcomes",
        description=(
            "Every pair has identical treated/control outcomes: all within-"
            "pair differences are zero."
        ),
        differences=(0.0, 0.0, 0.0, 0.0),
        reference={
            "n_pairs": 4,
            "randomization_set_size": 16,
            "null_statistic_observed": 0.0,
            # Every sign vector gives statistic 0 which ties the observed 0.
            "pvalue_effect_0": 1.0,
            # For tau != 0 the observed statistic is 4|tau|, the unique
            # maximum (all residuals point the same way), attained by exactly
            # 2 of the 16 sign vectors.
            "pvalue_effect_1": 2 / 16,
            "mean_difference": 0.0,
        },
    ),
    "balanced_small": Fixture(
        name="balanced_small",
        description=(
            "Three pairs, differences +1/-1/+1: treated wins two pairs and "
            "loses one. Exhaustive enumeration has 8 assignments."
        ),
        differences=(1.0, -1.0, 1.0),
        reference={
            "n_pairs": 3,
            "randomization_set_size": 8,
            "null_statistic_observed": 1.0,
            # Signed sums are +/-3 (2 vectors) and +/-1 (6 vectors); observed
            # |S|=1, so all 8 assignments are at least as extreme.
            "pvalue_effect_0": 1.0,
            "mean_difference": 1 / 3,
        },
    ),
    "extreme_differences": Fixture(
        name="extreme_differences",
        description=(
            "Four pairs with maximal, alternating within-pair differences "
            "+/-10. The observed signed sum (0) is the most central value."
        ),
        differences=(10.0, -10.0, 10.0, -10.0),
        reference={
            "n_pairs": 4,
            "randomization_set_size": 16,
            "null_statistic_observed": 0.0,
            "pvalue_effect_0": 1.0,
            # At tau=10 residuals are (0,-20,0,-20); the two zero residuals
            # are sign-invariant, so only 2 coins matter: all 4 sign choices
            # give |S| in {0,20,20,40}; observed |S|=40 ties 2 of them.
            "pvalue_effect_10": 8 / 16,
            "mean_difference": 0.0,
        },
    ),
    "one_extreme_pair": Fixture(
        name="one_extreme_pair",
        description=(
            "Four pairs; one pair shows a large difference (5) and three pairs "
            "show 1. Classic small-sample one-sided-flavor setup under the "
            "two-sided absolute-sum statistic."
        ),
        differences=(5.0, 1.0, 1.0, 1.0),
        reference={
            "n_pairs": 4,
            "randomization_set_size": 16,
            "null_statistic_observed": 8.0,
            # Signed sums: +/-8 (1 each), +/-6 (3 each), +/-4 (3 each),
            # +/-2 (1 each). |S| >= 8 only for the 2 extremes.
            "pvalue_effect_0": 2 / 16,
            "mean_difference": 2.0,
        },
    ),
    "monotone_differences": Fixture(
        name="monotone_differences",
        description="Five pairs with differences 1..5; generic non-symmetric case.",
        differences=(1.0, 2.0, 3.0, 4.0, 5.0),
        reference={
            "n_pairs": 5,
            "randomization_set_size": 32,
            "null_statistic_observed": 15.0,
            # Only the observed vector (+ + + + +) and its global flip attain
            # |S|=15.
            "pvalue_effect_0": 2 / 32,
            "mean_difference": 3.0,
        },
    ),
    "large_for_monte_carlo": Fixture(
        name="large_for_monte_carlo",
        description=(
            "30 pairs: 2**30 assignments exceed the exact-enumeration budget, "
            "forcing the Monte-Carlo path."
        ),
        differences=tuple(float((i % 7) - 3) for i in range(30)),
        reference={
            "n_pairs": 30,
            "randomization_set_size": 2 ** 30,
            "forces_method": "monte-carlo-signflip",
            "mean_difference": sum((i % 7) - 3 for i in range(30)) / 30,
        },
    ),
}


def get_fixture(name: str) -> Fixture:
    try:
        return FIXTURES[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown fixture {name!r}; available: {sorted(FIXTURES)}"
        ) from exc


def as_pairs(fixture: Fixture) -> list[tuple[float, float]]:
    """Render differences as concrete [treated, control] outcome pairs."""
    return [(float(d), 0.0) for d in fixture.differences]
