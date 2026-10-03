"""Substitution model registry.

Each model states its assumptions explicitly so results can be interpreted
against them. "Restricted substitution model" here means models that restrict
the general rate matrix: JC69 restricts all rates and frequencies to be equal;
K80 restricts frequencies to equality and rates to two classes
(transitions vs transversions).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    description: str
    assumptions: tuple[str, ...]
    valid_domain: str


MODELS: dict[str, ModelSpec] = {
    "p": ModelSpec(
        name="p",
        description="Uncorrected p-distance (observed mismatch fraction).",
        assumptions=(
            "No multiple-hit correction; observed differences equal substitutions.",
            "Valid sites only: positions where both sequences carry A/C/G/T.",
        ),
        valid_domain="0 <= p <= 1 (always defined once valid sites exist).",
    ),
    "jc69": ModelSpec(
        name="jc69",
        description="Jukes-Cantor 1969 one-parameter correction.",
        assumptions=(
            "All four bases occur at equal equilibrium frequency (0.25 each).",
            "All twelve substitution rates are equal.",
            "Substitutions accumulate as a homogeneous Poisson process per site.",
        ),
        valid_domain=(
            "p < 0.75. At p >= 0.75 the logarithm argument 1 - 4p/3 is <= 0 "
            "and the distance is reported as SATURATED, never as abs(log)."
        ),
    ),
    "k80": ModelSpec(
        name="k80",
        description="Kimura 1980 two-parameter correction.",
        assumptions=(
            "Equal equilibrium base frequencies (0.25 each).",
            "Two rate classes: transitions (A<->G, C<->T) and transversions.",
            "Substitutions accumulate as a homogeneous Poisson process per site.",
        ),
        valid_domain=(
            "1 - 2P - Q > 0 and 1 - 2Q > 0, with P the transition fraction and "
            "Q the transversion fraction. Outside this domain the distance is "
            "reported as SATURATED, never as abs(log)."
        ),
    ),
}


def get_model(name: str) -> ModelSpec:
    from .errors import InputValidationError

    try:
        return MODELS[name]
    except KeyError:
        raise InputValidationError(
            "unknown substitution model",
            detail={"model": name, "available": sorted(MODELS)},
        ) from None
