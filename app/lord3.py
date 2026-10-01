"""Pure estimation kernel: run a whole LORD 3 sequence in one call.

This is the offline entry point used by the reproducibility experiments; it
is deliberately separate from the online :mod:`app.contracts` ``step`` API.
Both execute the *same* frozen rule — this module only adds a vectorized
convenience wrapper and per-run aggregate statistics.  It never substitutes an
offline method (e.g. BH): every threshold still depends on past outcomes only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import Decision, LORD3State, step


@dataclass(frozen=True, slots=True)
class RunResult:
    decisions: tuple[Decision, ...]
    n_tests: int
    n_rejections: int
    final_wealth: float

    def rejection_indicator(self) -> list[int]:
        return [1 if d.rejected else 0 for d in self.decisions]


def run_sequence(
    hypothesis_ids: list[str],
    p_values: list[float],
    *,
    max_decisions: int | None = None,
) -> RunResult:
    """Apply LORD 3 decisions sequentially to a fixed ordered stream."""
    if len(hypothesis_ids) != len(p_values):
        raise ValueError("hypothesis_ids and p_values must have equal length")
    cap = max_decisions or len(p_values)
    state = LORD3State.initial(max_decisions=max(cap, len(p_values)))
    decisions: list[Decision] = []
    for hid, p in zip(hypothesis_ids, p_values):
        state, decision = step(state, hid, p)
        decisions.append(decision)
    return RunResult(
        decisions=tuple(decisions),
        n_tests=len(decisions),
        n_rejections=sum(1 for d in decisions if d.rejected),
        final_wealth=state.wealth,
    )
