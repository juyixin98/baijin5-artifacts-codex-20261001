"""Path constraints: fixed step pattern, slope constraint, Sakoe-Chiba window.

The behavior contract fixes these up front so alignments stay comparable:

* Steps: symmetric pattern {(1,1), (1,0), (0,1)} -- indices never decrease.
* Slope constraint: at most ``max_run`` consecutive non-diagonal steps in the
  same direction. This forbids unbounded horizontal/vertical runs, so one
  sequence can never be compressed onto a single frame of the other.
* Window: Sakoe-Chiba band, cells with |i - j| > window are forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass

# Step deltas: (di, dj). Diagonal first; the two non-diagonal directions are
# what the slope constraint throttles.
STEP_DIAG = (1, 1)
STEP_HORIZONTAL = (1, 0)  # advances query axis only
STEP_VERTICAL = (0, 1)  # advances reference axis only
STEPS = (STEP_DIAG, STEP_HORIZONTAL, STEP_VERTICAL)

# State indices inside the DP accumulator. State 0 is "last step diagonal";
# states 1..R are "horizontal run of length r"; states R+1..2R are "vertical
# run of length r". Runs longer than max_run simply have no state, which is
# what makes them unreachable.
DIAG_STATE = 0


def num_states(max_run: int) -> int:
    if max_run < 1:
        raise ValueError("max_run must be >= 1")
    return 1 + 2 * max_run


def horizontal_state(run: int) -> int:
    return run


def vertical_state(run: int, max_run: int) -> int:
    return max_run + run


@dataclass(frozen=True)
class PathConstraints:
    """Resolved constraints for one alignment."""

    window: int  # Sakoe-Chiba half-width, already resolved to a concrete int
    max_run: int  # max consecutive same-direction non-diagonal steps

    def __post_init__(self) -> None:
        if self.window < 0:
            raise ValueError("window must be >= 0")
        if self.max_run < 1:
            raise ValueError("max_run must be >= 1")

    def in_window(self, i: int, j: int) -> bool:
        return abs(i - j) <= self.window

    def endpoint_possible(self, n: int, m: int) -> bool:
        """Cheap necessary condition: the endpoint (n-1, m-1) must lie in band."""
        if n < 1 or m < 1:
            return False
        return abs((n - 1) - (m - 1)) <= self.window


def check_path_legal(
    path: list[tuple[int, int]],
    n: int,
    m: int,
    constraints: PathConstraints,
) -> bool:
    """Verify a path satisfies every constraint. Used by tests and diagnostics."""
    if not path or path[0] != (0, 0) or path[-1] != (n - 1, m - 1):
        return False
    run_dir: tuple[int, int] | None = None
    run_len = 0
    for (pi, pj), (ci, cj) in zip(path, path[1:]):
        step = (ci - pi, cj - pj)
        if step not in STEPS:
            return False
        if not constraints.in_window(ci, cj):
            return False
        if step == STEP_DIAG:
            run_dir, run_len = None, 0
        elif step == run_dir:
            run_len += 1
            if run_len > constraints.max_run:
                return False
        else:
            run_dir, run_len = step, 1
    return True
