"""Evidence and diagnostic helpers.

Two distinct things live here and must not be conflated:

* **Audit evidence** -- replay of a committed run (thresholds derivable from
  past results only, hash chain intact). This is provided by the store.
* **Empirical error accounting** -- once ground-truth labels are available
  (simulation only; never known for real hypotheses), classify rejections into
  false discoveries V and true discoveries S and compute FDP.

Nothing here promises per-run FDR control: FDR = E[V / max(R,1)] is observable
only by aggregating many independent replications.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from .errors import InputValidationError


@dataclass(frozen=True)
class DiscoveryAccount:
    n_tests: int
    n_null: int
    n_alternative: int
    rejections: int          # R
    false_discoveries: int   # V: rejected while null
    true_discoveries: int    # S: rejected while alternative
    fdp: float               # V / max(R, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_tests": self.n_tests,
            "n_null": self.n_null,
            "n_alternative": self.n_alternative,
            "rejections": self.rejections,
            "false_discoveries": self.false_discoveries,
            "true_discoveries": self.true_discoveries,
            "fdp": self.fdp,
        }


def account(rejected: Sequence[bool], is_alternative: Sequence[bool]) -> DiscoveryAccount:
    if len(rejected) != len(is_alternative):
        raise InputValidationError(
            "decisions and truth labels must have equal length",
            details={"decisions": len(rejected), "labels": len(is_alternative)},
        )
    n = len(rejected)
    r = sum(1 for x in rejected if x)
    v = sum(1 for dec, alt in zip(rejected, is_alternative) if dec and not alt)
    s = sum(1 for dec, alt in zip(rejected, is_alternative) if dec and alt)
    n_alt = sum(1 for x in is_alternative if x)
    return DiscoveryAccount(
        n_tests=n,
        n_null=n - n_alt,
        n_alternative=n_alt,
        rejections=r,
        false_discoveries=v,
        true_discoveries=s,
        fdp=v / max(r, 1),
    )


def aggregate_fdr(accounts: Sequence[DiscoveryAccount]) -> dict[str, float]:
    """Aggregate per-run accounts into empirical FDR and pooled power."""
    if not accounts:
        raise InputValidationError("cannot aggregate zero accounts", details={})
    fdps = [a.fdp for a in accounts]
    mean = sum(fdps) / len(fdps)
    var = (
        sum((x - mean) ** 2 for x in fdps) / (len(fdps) - 1)
        if len(fdps) > 1
        else 0.0
    )
    se = (var / len(fdps)) ** 0.5
    total_r = sum(a.rejections for a in accounts)
    total_v = sum(a.false_discoveries for a in accounts)
    total_s = sum(a.true_discoveries for a in accounts)
    total_alt = sum(a.n_alternative for a in accounts)
    total_null = sum(a.n_null for a in accounts)
    return {
        "n_runs": float(len(accounts)),
        "fdr_estimate": mean,
        "fdr_se": se,
        "ci95_low": max(0.0, mean - 1.96 * se),
        "ci95_high": mean + 1.96 * se,
        "pooled_fdp": total_v / max(total_r, 1),
        "pooled_power": (total_s / total_alt) if total_alt else 0.0,
        "total_tests": float(sum(a.n_tests for a in accounts)),
        "total_null": float(total_null),
        "total_alternative": float(total_alt),
        "total_rejections": float(total_r),
        "total_false_discoveries": float(total_v),
    }


def explain_decision(step: dict[str, Any]) -> str:
    """Human-readable rationale tying a decision to its causal inputs."""
    if step.get("status") != "decided":
        return f"slot {step['idx']}: threshold pre-committed, p-value not yet observed"
    rel = "<=" if step["rejected"] else ">"
    verb = "REJECT" if step["rejected"] else "do not reject"
    return (
        f"slot {step['idx']} ({step['hypothesis_id']}): "
        f"alpha_t={step['threshold']:.6g} was frozen BEFORE observing p_t; "
        f"p_t={step['p_value']:.6g} {rel} alpha_t -> {verb} the null."
    )


def dump_event_trail(events: Sequence[dict[str, Any]], path: str) -> None:
    """Persist an event trail (JSON) so an incident can be replayed."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(list(events), fh, indent=2, sort_keys=True, default=str)
