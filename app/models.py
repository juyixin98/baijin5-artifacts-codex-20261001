"""Pydantic contracts for the NJ API.

These models are the data contract between the HTTP boundary and the
pipeline. Everything crossing a module boundary is one of these models or a
plain ``dict`` produced by ``model_dump``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NegativeBranchMode = Literal["allow", "clamp", "error"]
InputFormat = Literal["fasta", "matrix"]


class MatrixInput(BaseModel):
    """Square distance matrix with explicit labels."""

    labels: list[str]
    distances: list[list[float]]


class TreeOptions(BaseModel):
    """Declared behavior switches.

    negative_branch_mode:
        allow -- keep negative branch lengths in the output and report them
                 as ``negative_branch`` events (default; nothing is hidden).
        clamp -- set negative branch lengths to 0.0, emitting a
                 ``branch_clamped`` event per correction so the distortion is
                 visible in the provenance record and in the residual report.
        error -- abort with COMPUTATION_FAILED on the first negative branch.
    """

    negative_branch_mode: NegativeBranchMode = "allow"
    max_taxa: int = Field(default=500, ge=2, le=100000)


class TreeRequest(BaseModel):
    """Build request. Exactly one of ``sequences`` / ``matrix`` is required,
    matching ``input_format``."""

    request_id: str | None = Field(
        default=None,
        description="Optional idempotency key. Replaying the same key with "
        "the same payload returns the stored result; replaying it with a "
        "different payload is a STATE_CONFLICT.",
    )
    input_format: InputFormat
    sequences: str | None = Field(
        default=None, description="FASTA text; required when input_format=fasta."
    )
    matrix: MatrixInput | None = Field(
        default=None, description="Required when input_format=matrix."
    )
    options: TreeOptions = Field(default_factory=TreeOptions)


class NJEvent(BaseModel):
    """A decision recorded during the run (tie, clamp, kept negative branch).

    ``rationale`` states the rule that produced the decision so the log can
    be replayed without reading the code.
    """

    type: str
    round: int | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class PairResidual(BaseModel):
    taxon_i: str
    taxon_j: str
    observed: float
    tree: float
    abs_diff: float


class ResidualReport(BaseModel):
    """Fit of the output tree against the observed distances.

    Computed from patristic (path) distances on the emitted tree, so a
    clamped negative branch shows up here as residual — it cannot be hidden.
    """

    sum_abs: float
    max_abs: float
    mean_abs: float
    rms: float
    per_pair: list[PairResidual]


class TreeResponse(BaseModel):
    run_id: str
    status: Literal["ok"] = "ok"
    replayed: bool = False
    newick: str
    leaf_map: dict[str, str] = Field(
        description="Newick leaf name -> original input label."
    )
    residuals: ResidualReport
    events: list[NJEvent]
    warnings: list[str] = Field(default_factory=list)


class ErrorBody(BaseModel):
    category: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
