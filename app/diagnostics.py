"""Evidence verification and discovery diagnostics.

Two independent concerns:

* :func:`replay_verify` audits a persisted run.  It rebuilds the ENTIRE
  trajectory by feeding stored p-values back through the pure kernel
  (:func:`app.contracts.replay`) and (a) recomputes every threshold/wealth
  value, (b) rechecks every hash-chain link.  The audit path never trusts the
  hot counters on the run row.
* :func:`discovery_stats` / :func:`summarize_replications` turn decisions with
  known ground-truth labels (synthetic fixtures only) into false-discovery
  statistics.  These are empirical averages over replications, NOT a claim
  that any single run is guaranteed below alpha.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .contracts import CONTRACT_FINGERPRINT, W0, Decision, replay
from .errors import EvidenceTamperedError
from .store import GENESIS_HASH, SQLiteStore, row_hash

_FLOAT_FIELDS = (
    "p_value",
    "threshold",
    "gamma_value",
    "wealth_before",
    "wealth_after",
    "w_tau_used",
)
_INT_FIELDS = ("idx", "tau")
_REL_TOL = 1e-12
_ABS_TOL = 1e-15


@dataclass(frozen=True, slots=True)
class Mismatch:
    idx: int
    field_name: str
    stored: object
    replayed: object

    def to_dict(self) -> dict:
        return {
            "idx": self.idx,
            "field": self.field_name,
            "stored": self.stored,
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class ReplayReport:
    run_id: str
    ok: bool
    n_decisions: int
    chain_ok: bool
    run_counters_ok: bool
    contract_fingerprint_ok: bool
    mismatches: tuple[Mismatch, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "n_decisions": self.n_decisions,
            "chain_ok": self.chain_ok,
            "run_counters_ok": self.run_counters_ok,
            "contract_fingerprint_ok": self.contract_fingerprint_ok,
            "mismatches": [m.to_dict() for m in self.mismatches],
        }


def _floats_close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)


def replay_verify(
    store: SQLiteStore, run_id: str, *, raise_on_mismatch: bool = True
) -> ReplayReport:
    """Independently rebuild a run from stored evidence and compare."""
    meta = store.get_run(run_id)
    rows = store.all_decision_rows(run_id)

    fingerprint_ok = meta.contract_fingerprint == CONTRACT_FINGERPRINT
    mismatches: list[Mismatch] = []
    replayed: list[Decision] = []
    replay_error: str | None = None
    try:
        replayed = replay([(r["hypothesis_id"], r["p_value"]) for r in rows])
    except Exception as exc:  # kernel rejects malformed/duplicate evidence
        replay_error = f"{type(exc).__name__}: {exc}"

    if replay_error is None and len(replayed) == len(rows):
        for row, dec in zip(rows, replayed):
            stored_vals = {
                "idx": row["idx"],
                "tau": row["tau"],
                "p_value": row["p_value"],
                "threshold": row["threshold"],
                "gamma_value": row["gamma_value"],
                "wealth_before": row["wealth_before"],
                "wealth_after": row["wealth_after"],
                "w_tau_used": row["w_tau_used"],
            }
            expected_vals = {
                "idx": dec.index,
                "tau": dec.tau,
                "p_value": dec.p_value,
                "threshold": dec.threshold,
                "gamma_value": dec.gamma_value,
                "wealth_before": dec.wealth_before,
                "wealth_after": dec.wealth_after,
                "w_tau_used": dec.w_tau_used,
            }
            if row["hypothesis_id"] != dec.hypothesis_id:
                mismatches.append(
                    Mismatch(dec.index, "hypothesis_id",
                             row["hypothesis_id"], dec.hypothesis_id)
                )
            if bool(row["rejected"]) != dec.rejected:
                mismatches.append(
                    Mismatch(dec.index, "rejected",
                             bool(row["rejected"]), dec.rejected)
                )
            for name in _FLOAT_FIELDS:
                if not _floats_close(stored_vals[name], expected_vals[name]):
                    mismatches.append(
                        Mismatch(dec.index, name,
                                 stored_vals[name], expected_vals[name])
                    )
            for name in _INT_FIELDS:
                if stored_vals[name] != expected_vals[name]:
                    mismatches.append(
                        Mismatch(dec.index, name,
                                 stored_vals[name], expected_vals[name])
                    )
    else:
        mismatches.append(
            Mismatch(
                0,
                "replay",
                replay_error or "row count differs",
                f"{len(replayed)} replayed vs {len(rows)} stored",
            )
        )

    # Independent hash-chain check.
    chain_ok = True
    prev = GENESIS_HASH
    for row in rows:
        if row["prev_hash"] != prev:
            chain_ok = False
            mismatches.append(
                Mismatch(row["idx"], "prev_hash", row["prev_hash"], prev)
            )
        dec = Decision(
            index=row["idx"],
            hypothesis_id=row["hypothesis_id"],
            p_value=row["p_value"],
            threshold=row["threshold"],
            gamma_value=row["gamma_value"],
            rejected=bool(row["rejected"]),
            wealth_before=row["wealth_before"],
            wealth_after=row["wealth_after"],
            tau=row["tau"],
            w_tau_used=row["w_tau_used"],
            reason=row["reason"],
        )
        expected_hash = row_hash(
            run_id, dec, prev, meta.contract_fingerprint
        )
        if row["row_hash"] != expected_hash:
            chain_ok = False
            mismatches.append(
                Mismatch(row["idx"], "row_hash", row["row_hash"], expected_hash)
            )
        prev = row["row_hash"]

    expected_tau, expected_w_tau = _terminal_anchor(replayed)
    counters_ok = (
        meta.last_index == len(replayed)
        and meta.tau == expected_tau
        and _floats_close(meta.w_tau, expected_w_tau)
        and _floats_close(
            meta.wealth,
            replayed[-1].wealth_after if replayed else W0,
        )
    )

    ok = (
        fingerprint_ok
        and chain_ok
        and counters_ok
        and not mismatches
        and replay_error is None
    )
    report = ReplayReport(
        run_id=run_id,
        ok=ok,
        n_decisions=len(rows),
        chain_ok=chain_ok,
        run_counters_ok=counters_ok,
        contract_fingerprint_ok=fingerprint_ok,
        mismatches=tuple(mismatches[:20]),
    )
    if not ok and raise_on_mismatch:
        raise EvidenceTamperedError(report.to_dict())
    return report


def _terminal_anchor(decisions: list[Decision]) -> tuple[int, float]:
    """Reconstruct the (tau, W(tau)) hot anchor from rebuilt decisions."""
    last_rejection = next(
        (d for d in reversed(decisions) if d.rejected), None
    )
    if last_rejection is None:
        return 0, W0
    return last_rejection.index, last_rejection.wealth_after


@dataclass(frozen=True, slots=True)
class DiscoveryStats:
    n_tests: int
    n_null: int
    n_nonnull: int
    n_rejections: int          # R
    false_discoveries: int     # V = rejected nulls
    fdp: float                 # V/R, defined as 0.0 when R == 0
    power: float               # (R - V) / n_nonnull, 0.0 if no non-nulls
    any_rejection: bool

    def to_dict(self) -> dict:
        return {
            "n_tests": self.n_tests,
            "n_null": self.n_null,
            "n_nonnull": self.n_nonnull,
            "n_rejections": self.n_rejections,
            "false_discoveries": self.false_discoveries,
            "fdp": self.fdp,
            "power": self.power,
            "any_rejection": self.any_rejection,
        }


def discovery_stats(decisions: list[Decision], is_null: list[bool]) -> DiscoveryStats:
    """Count false discoveries against known labels (synthetic data only)."""
    if len(decisions) != len(is_null):
        raise ValueError("one null label per decision is required")
    rejected = [d.rejected for d in decisions]
    r = sum(rejected)
    v = sum(1 for rej, null in zip(rejected, is_null) if rej and null)
    n_null = sum(1 for x in is_null if x)
    n_nonnull = len(is_null) - n_null
    return DiscoveryStats(
        n_tests=len(decisions),
        n_null=n_null,
        n_nonnull=n_nonnull,
        n_rejections=r,
        false_discoveries=v,
        fdp=(v / r if r > 0 else 0.0),
        power=((r - v) / n_nonnull if n_nonnull > 0 else 0.0),
        any_rejection=r > 0,
    )


@dataclass(frozen=True, slots=True)
class ReplicationSummary:
    n_replications: int
    fdr_estimate: float          # mean of per-run FDP (0 when R=0)
    pr_any_rejection: float
    mean_rejections: float
    mean_false_discoveries: float
    mean_power: float
    target_fdr: float
    seed: int
    stream_kind: str
    stream_params: dict

    def to_dict(self) -> dict:
        return {
            "n_replications": self.n_replications,
            "fdr_estimate": self.fdr_estimate,
            "pr_any_rejection": self.pr_any_rejection,
            "mean_rejections": self.mean_rejections,
            "mean_false_discoveries": self.mean_false_discoveries,
            "mean_power": self.mean_power,
            "target_fdr": self.target_fdr,
            "seed": self.seed,
            "stream_kind": self.stream_kind,
            "stream_params": self.stream_params,
            "interpretation": (
                "Empirical average across independent replications. It "
                "estimates FDR=E[V/R] (FDP=0 on runs with no rejections); "
                "a single replication proves nothing, and Monte Carlo error "
                "means the estimate can exceed alpha by chance even when the "
                "rule is valid."
            ),
        }


def summarize_replications(stats: list[DiscoveryStats], *, seed: int,
                           stream_kind: str, stream_params: dict,
                           target_fdr: float = 0.05) -> ReplicationSummary:
    if not stats:
        raise ValueError("at least one replication is required")
    n = len(stats)
    return ReplicationSummary(
        n_replications=n,
        fdr_estimate=sum(s.fdp for s in stats) / n,
        pr_any_rejection=sum(1 for s in stats if s.any_rejection) / n,
        mean_rejections=sum(s.n_rejections for s in stats) / n,
        mean_false_discoveries=sum(s.false_discoveries for s in stats) / n,
        mean_power=sum(s.power for s in stats) / n,
        target_fdr=target_fdr,
        seed=seed,
        stream_kind=stream_kind,
        stream_params=dict(stream_params),
    )
