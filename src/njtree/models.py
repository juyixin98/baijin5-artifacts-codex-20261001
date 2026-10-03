"""Data contracts shared across module boundaries.

These dataclasses are the *only* objects that cross module boundaries:
parsing produces ``DistanceMatrix`` (via matrix validation), the NJ core
consumes it and produces ``TreeNode`` + ``JoinStep`` + ``NegativeBranchEvent``,
residuals consume tree distances and produce ``ResidualReport``, and the
service bundles everything into ``BuildResult``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

DEFAULT_MAX_TAXA = 500


class NegativeBranchMode(str, Enum):
    """Declared handling for negative branch length estimates.

    ERROR  -- raise ComputationError (nothing is hidden, no tree is produced)
    REPORT -- keep the negative value in the tree/Newick and record an event
    CLAMP  -- set the branch to 0.0, record an event with the original value;
              residuals are computed against the *clamped* tree so the fit
              error introduced by clamping stays visible
    """

    ERROR = "error"
    REPORT = "report"
    CLAMP = "clamp"


@dataclass(frozen=True)
class DistanceMatrix:
    """A validated distance matrix. Construction goes through
    ``matrix.validate_distance_matrix`` -- do not build directly from
    untrusted data."""

    labels: tuple[str, ...]
    values: np.ndarray  # float64, shape (n, n), symmetric, zero diagonal

    @property
    def n(self) -> int:
        return len(self.labels)


@dataclass(frozen=True)
class BuildParams:
    negative_branch_mode: NegativeBranchMode = NegativeBranchMode.REPORT
    max_taxa: int = DEFAULT_MAX_TAXA
    run_id: str | None = None  # client-supplied id for idempotent submission


@dataclass
class JoinStep:
    """One recorded NJ decision. ``is_final`` marks the terminal step where
    the three remaining nodes are attached to the root (no Q criterion)."""

    step_index: int
    active_ids: list[int]
    chosen_ids: list[int]
    chosen_labels: list[str]
    q_value: float | None
    tie_count: int | None
    limbs: list[float]  # applied (post-mode) branch lengths
    limb_originals: list[float]  # raw estimates before mode handling
    is_final: bool = False


@dataclass
class NegativeBranchEvent:
    step_index: int
    node_id: int
    node_label: str
    original: float
    applied: float
    mode: str


@dataclass
class PairResidual:
    label_a: str
    label_b: str
    input_distance: float
    fitted_distance: float
    residual: float  # input - fitted (signed)


@dataclass
class ResidualReport:
    pairs: list[PairResidual]
    total_absolute: float
    max_absolute: float
    rmse: float


@dataclass
class BuildResult:
    run_id: str
    newick: str
    leaf_map: dict[str, int]  # leaf label -> stable leaf node id
    residuals: ResidualReport
    steps: list[JoinStep]
    negative_events: list[NegativeBranchEvent]
    params: BuildParams
    idempotent: bool = False  # True when returned from an existing identical run


@dataclass
class ReplayReport:
    run_id: str
    match: bool
    differences: list[str] = field(default_factory=list)
