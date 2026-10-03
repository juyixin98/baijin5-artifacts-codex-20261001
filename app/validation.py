"""Topology validation: compare an original binary image with its skeleton.

Metrics use the compatible adjacency pair from the kernel contract:
foreground components under 8-connectivity, background holes under
4-connectivity (a hole is a background component that does not touch the
image border). Endpoint counts use the 8-neighbour degree.

Findings are split into two buckets:
- ``failures``: hard contract violations (component/hole count changed,
  foreground erased, endpoint lost).
- ``uncertain``: conclusions the metrics cannot firmly decide (empty
  foreground, endpoint count grew, thinning did not converge).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import ndimage

FG_STRUCTURE = np.ones((3, 3), dtype=int)  # 8-connectivity
BG_STRUCTURE = ndimage.generate_binary_structure(2, 1)  # 4-connectivity


def count_components(image: np.ndarray) -> int:
    """Foreground connected components under 8-connectivity."""
    _, count = ndimage.label(np.asarray(image).astype(bool), structure=FG_STRUCTURE)
    return int(count)


def count_holes(image: np.ndarray) -> int:
    """Background components (4-connectivity) not touching the border."""
    img = np.asarray(image).astype(bool)
    labels, _ = ndimage.label(~img, structure=BG_STRUCTURE)
    if labels.size == 0:
        return 0
    border_labels = np.unique(
        np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])
    )
    interior = set(np.unique(labels)) - set(border_labels.tolist()) - {0}
    return len(interior)


def count_endpoints(image: np.ndarray) -> int:
    """Foreground pixels with exactly one 8-neighbour."""
    img = np.asarray(image).astype(bool)
    kernel = np.ones((3, 3), dtype=int)
    kernel[1, 1] = 0
    deg = ndimage.convolve(img.astype(int), kernel, mode="constant", cval=0)
    return int(np.count_nonzero(img & (deg == 1)))


@dataclass(frozen=True)
class TopologyReport:
    components_before: int
    components_after: int
    holes_before: int
    holes_after: int
    endpoints_before: int
    endpoints_after: int
    converged: bool
    failures: tuple[str, ...] = field(default_factory=tuple)
    uncertain: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        data = asdict(self)
        data["passed"] = self.passed
        return data


def topology_report(
    original: np.ndarray,
    skeleton: np.ndarray,
    converged: bool = True,
) -> TopologyReport:
    """Build the before/after topology report for a thinning run."""
    orig = np.asarray(original).astype(bool)
    skel = np.asarray(skeleton).astype(bool)

    components_before = count_components(orig)
    components_after = count_components(skel)
    holes_before = count_holes(orig)
    holes_after = count_holes(skel)
    endpoints_before = count_endpoints(orig)
    endpoints_after = count_endpoints(skel)

    failures: list[str] = []
    uncertain: list[str] = []

    if components_after != components_before:
        failures.append(
            f"component_count_changed:{components_before}->{components_after}"
        )
    if holes_after != holes_before:
        failures.append(f"hole_count_changed:{holes_before}->{holes_after}")
    if orig.any() and not skel.any():
        failures.append("foreground_erased:non-empty input produced empty skeleton")
    if endpoints_after < endpoints_before:
        failures.append(f"endpoint_lost:{endpoints_before}->{endpoints_after}")

    if not orig.any():
        uncertain.append("empty_foreground:topology checks are vacuous")
    if endpoints_after > endpoints_before:
        uncertain.append(
            f"endpoint_count_increased:{endpoints_before}->{endpoints_after}"
        )
    if not converged:
        uncertain.append("not_converged:max_rounds reached before a clean round")

    return TopologyReport(
        components_before=components_before,
        components_after=components_after,
        holes_before=holes_before,
        holes_after=holes_after,
        endpoints_before=endpoints_before,
        endpoints_after=endpoints_after,
        converged=converged,
        failures=tuple(failures),
        uncertain=tuple(uncertain),
    )
