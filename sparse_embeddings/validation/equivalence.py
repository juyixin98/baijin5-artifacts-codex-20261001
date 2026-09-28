"""Sparse/dense equivalence verification with explicit, typed verdicts.

Given the same initial weights and the same raw batches, the sparse service
and the independent dense reference must agree on:

* every weight row (touched *and* untouched),
* every momentum row,
* per-row step counters,
* global step,
* per-batch norms / clip scales / touched sets.

Failures are reported per field with the offending row ids and absolute
errors - never collapsed into a boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from sparse_embeddings.state import StepResult, TableState
from sparse_embeddings.validation.dense_reference import DenseReferenceModel

DEFAULT_RTOL = 1e-10
DEFAULT_ATOL = 1e-10


@dataclass
class EquivalenceReport:
    matched: bool
    rtol: float
    atol: float
    failures: list[dict] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    def raise_if_failed(self) -> None:
        if not self.matched:
            parts = ["sparse/dense mismatch:"]
            for f in self.failures:
                parts.append(
                    f"  - {f['field']}: {f['message']}"
                )
            raise AssertionError("\n".join(parts))

    def summary(self) -> str:
        if self.matched:
            return f"MATCH ({len(self.checks)} checks)"
        return f"MISMATCH ({len(self.failures)} failures)"


def _compare_matrix(
    name: str,
    sparse_mat: np.ndarray,
    dense_mat: np.ndarray,
    failures: list[dict],
    *,
    rtol: float,
    atol: float,
) -> str:
    if sparse_mat.shape != dense_mat.shape:
        failures.append(
            {
                "field": name,
                "message": f"shape mismatch {sparse_mat.shape} vs {dense_mat.shape}",
            }
        )
        return name
    abs_err = np.abs(sparse_mat - dense_mat)
    tol = atol + rtol * np.abs(dense_mat)
    bad_rows = np.nonzero(np.any(abs_err > tol, axis=1))[0]
    if bad_rows.size:
        worst = int(np.argmax(np.max(abs_err, axis=1)))
        failures.append(
            {
                "field": name,
                "message": (
                    f"{bad_rows.size} rows differ beyond tolerance; "
                    f"first rows {bad_rows[:8].tolist()}; "
                    f"max abs err {abs_err[worst].max():.3e} at row {worst}"
                ),
            }
        )
    return name


def compare_states(
    table: TableState,
    reference: DenseReferenceModel,
    *,
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> EquivalenceReport:
    failures: list[dict] = []
    checks: list[str] = []
    checks.append(
        _compare_matrix(
            "weights", table.weights, reference.weights, failures, rtol=rtol, atol=atol
        )
    )
    checks.append(
        _compare_matrix(
            "momentum",
            table.momentum,
            reference.momentum,
            failures,
            rtol=rtol,
            atol=atol,
        )
    )
    if not np.array_equal(table.row_steps, reference.row_steps):
        diff_rows = np.nonzero(table.row_steps != reference.row_steps)[0]
        failures.append(
            {
                "field": "row_steps",
                "message": (
                    f"{diff_rows.size} rows differ; first {diff_rows[:8].tolist()}"
                ),
            }
        )
    checks.append("row_steps")
    if table.global_step != reference.global_step:
        failures.append(
            {
                "field": "global_step",
                "message": f"{table.global_step} vs {reference.global_step}",
            }
        )
    checks.append("global_step")
    return EquivalenceReport(
        matched=not failures, rtol=rtol, atol=atol, failures=failures, checks=checks
    )


def assert_sparse_matches_dense(
    table: TableState,
    reference: DenseReferenceModel,
    *,
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_RTOL,
) -> EquivalenceReport:
    report = compare_states(table, reference, rtol=rtol, atol=atol)
    report.raise_if_failed()
    return report


def assert_step_matches_reference(
    result: StepResult,
    ref_outcome: dict,
    *,
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> None:
    """Compare one batch's reported decision basis against dense reference."""
    assert result.stepped == bool(ref_outcome["stepped"]), (
        f"stepped flag {result.stepped} != {ref_outcome['stepped']}"
    )
    if not result.stepped:
        return
    np.testing.assert_array_equal(
        np.sort(result.touched_indices),
        np.sort(ref_outcome["touched"]),
        err_msg="touched row sets differ",
    )
    np.testing.assert_allclose(
        result.pre_clip_global_norm,
        ref_outcome["pre_norm"],
        rtol=rtol,
        atol=atol,
        err_msg="pre-clip global norms differ",
    )
    np.testing.assert_allclose(
        result.post_clip_global_norm,
        ref_outcome["post_norm"],
        rtol=rtol,
        atol=atol,
        err_msg="post-clip norms differ",
    )
    assert result.clip_applied == bool(ref_outcome["clipped"]), (
        f"clip_applied {result.clip_applied} != {ref_outcome['clipped']}"
    )
    if result.n_unique_touched > 0:
        np.testing.assert_allclose(
            result.per_row_scales,
            ref_outcome["scales"],
            rtol=rtol,
            atol=atol,
            err_msg="per-row clip scales differ",
        )
