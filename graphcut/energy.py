"""Energy model: contract validation and independent evaluation.

This module is deliberately independent of the graph construction and the
solver. Tests enumerate labelings and score them with
:func:`evaluate_energy` so the reference answers never come from the code
path under test.
"""

from __future__ import annotations

import numpy as np

from .errors import InputValidationError
from .models import EnergyDecomposition, EnergySpec, PairwiseTerm

SUBMODULARITY_TOL = 1e-12


def validate_unaries(unary0: np.ndarray, unary1: np.ndarray) -> None:
    """Data terms must be finite and non-negative."""
    for name, arr in (("unary0", unary0), ("unary1", unary1)):
        if arr.ndim != 2 or arr.shape != unary0.shape:
            raise InputValidationError(
                "bad_unary_shape",
                f"{name} shape {arr.shape} does not match unary0 {unary0.shape}",
            )
        if not np.all(np.isfinite(arr)):
            raise InputValidationError(
                "bad_unary_values", f"{name} contains NaN or infinite values"
            )
        minimum = float(arr.min())
        if minimum < 0.0:
            raise InputValidationError(
                "negative_data_term",
                f"{name} contains negative data term {minimum}; "
                "data terms must be non-negative",
                details={"min": minimum},
            )


def validate_pairwise(term: PairwiseTerm) -> None:
    """Smoothness terms must be finite, non-negative and submodular.

    Submodularity for two labels: ``v00 + v11 <= v01 + v10``. A violation is
    rejected outright — taking absolute values or clamping would change the
    energy being minimized, so we never do it.
    """
    values = (term.v00, term.v01, term.v10, term.v11)
    if not all(np.isfinite(values)):
        raise InputValidationError(
            "bad_pairwise_values",
            f"pairwise term ({term.p}, {term.q}) has NaN or infinite entries",
        )
    minimum = min(values)
    if minimum < 0.0:
        raise InputValidationError(
            "negative_smoothness_term",
            f"pairwise term ({term.p}, {term.q}) has negative entry {minimum}; "
            "smoothness terms must be non-negative",
            details={"p": term.p, "q": term.q, "min": minimum},
        )
    slack = term.v01 + term.v10 - term.v00 - term.v11
    if slack < -SUBMODULARITY_TOL:
        raise InputValidationError(
            "non_submodular_pairwise",
            f"pairwise term ({term.p}, {term.q}) is not submodular: "
            f"v00+v11={term.v00 + term.v11} > v01+v10={term.v01 + term.v10}; "
            "s-t graph cuts cannot minimize supermodular energies exactly",
            details={
                "p": term.p,
                "q": term.q,
                "v00": term.v00,
                "v01": term.v01,
                "v10": term.v10,
                "v11": term.v11,
            },
        )


def validate_spec(spec: EnergySpec) -> None:
    """Validate the whole energy contract before any solving happens."""
    expected_shape = (spec.height, spec.width)
    if spec.unary0.shape != expected_shape or spec.unary1.shape != expected_shape:
        raise InputValidationError(
            "bad_unary_shape",
            f"unary arrays must have shape {expected_shape}, got "
            f"{spec.unary0.shape} / {spec.unary1.shape}",
        )
    validate_unaries(spec.unary0, spec.unary1)
    n = spec.num_pixels
    for term in spec.pairwise:
        if not (0 <= term.p < n and 0 <= term.q < n) or term.p == term.q:
            raise InputValidationError(
                "bad_pairwise_endpoint",
                f"pairwise term references invalid pixels ({term.p}, {term.q}) "
                f"for {n} pixels",
            )
        validate_pairwise(term)
    for label, indices in (("foreground", spec.seeds.foreground),
                           ("background", spec.seeds.background)):
        for idx in indices:
            if not 0 <= idx < n:
                raise InputValidationError(
                    "bad_seed_index",
                    f"{label} seed index {idx} out of range for {n} pixels",
                )


def evaluate_energy(spec: EnergySpec, labeling: np.ndarray) -> EnergyDecomposition:
    """Score a labeling directly from the spec — no graph machinery involved.

    ``labeling`` is an (H, W) array of 0/1. This is the reference
    implementation used by the enumeration tests and the ``/validate``
    endpoint.
    """
    labels = np.asarray(labeling)
    if labels.shape != (spec.height, spec.width):
        raise InputValidationError(
            "bad_labeling_shape",
            f"labeling shape {labels.shape} != {(spec.height, spec.width)}",
        )
    if not np.isin(labels, (0, 1)).all():
        raise InputValidationError(
            "bad_labeling_values", "labeling must contain only 0 and 1"
        )
    flat = labels.reshape(-1).astype(np.int8)
    u0 = spec.unary0.reshape(-1)
    u1 = spec.unary1.reshape(-1)
    data = float(np.where(flat == 1, u1, u0).sum())

    smoothness = 0.0
    for term in spec.pairwise:
        xp, xq = int(flat[term.p]), int(flat[term.q])
        smoothness += _term_cost(term, xp, xq)
    return EnergyDecomposition(data=data, smoothness=smoothness,
                               total=data + smoothness)


def _term_cost(term: PairwiseTerm, xp: int, xq: int) -> float:
    if xp == 0 and xq == 0:
        return term.v00
    if xp == 0 and xq == 1:
        return term.v01
    if xp == 1 and xq == 0:
        return term.v10
    return term.v11
