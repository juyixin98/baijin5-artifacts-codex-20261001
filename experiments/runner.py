"""Reproducible Monte Carlo experiments (teaching fixtures only).

Every experiment run writes a JSONL event log plus a JSON summary into an
artifacts directory.  Each replication record contains:

* ``run_no``          - replication number,
* ``seed``            - the exact RNG seed (re-runnable),
* stream parameters and ground-truth counts,
* ``key_states``      - threshold/wealth/rejection state at checkpoints,
* ``stats``           - R, V, FDP, power for that replication,
* ``judgement``       - why the replication counts as it does.

The log exists to make a surprising result replayable: re-seeding the stream
generator with ``seed`` reproduces every p-value and hence every row.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable

from app.contracts import ALPHA
from app.diagnostics import (
    DiscoveryStats,
    discovery_stats,
    summarize_replications,
)
from app.lord3 import run_sequence
from app.simulation import PValueStream, mixed_stream, null_stream

CHECKPOINT_INDICES = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000)


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    name: str
    n_replications: int
    n_tests: int
    base_seed: int
    stream_factory: Callable[[int], PValueStream]
    stream_kind: str
    stream_params: dict


def null_experiment(
    n_replications: int = 500, n_tests: int = 500, base_seed: int = 20260929
) -> ExperimentConfig:
    return ExperimentConfig(
        name="global_null_uniform",
        n_replications=n_replications,
        n_tests=n_tests,
        base_seed=base_seed,
        stream_factory=lambda seed: null_stream(n_tests, seed),
        stream_kind="null_uniform",
        stream_params={"n_tests": n_tests},
    )


def mixed_experiment(
    n_replications: int = 500,
    n_tests: int = 500,
    base_seed: int = 20260929,
    nonnull_fraction: float = 0.2,
    beta_a: float = 0.05,
    block: bool = False,
) -> ExperimentConfig:
    params = {
        "n_tests": n_tests,
        "nonnull_fraction": nonnull_fraction,
        "beta_a": beta_a,
        "block": block,
    }
    return ExperimentConfig(
        name=f"mixed_f{nonnull_fraction}_beta{beta_a}"
        + ("_block" if block else ""),
        n_replications=n_replications,
        n_tests=n_tests,
        base_seed=base_seed,
        stream_factory=lambda seed: mixed_stream(
            n_tests, nonnull_fraction, seed, beta_a=beta_a, block=block
        ),
        stream_kind="mixed_uniform_beta",
        stream_params=params,
    )


def _seed_for(base_seed: int, run_no: int) -> int:
    # Independent, deterministic per-replication seeds.
    return base_seed * 1_000_003 + run_no


def _key_states(decisions) -> list[dict]:
    wanted = {i for i in CHECKPOINT_INDICES if i <= len(decisions)}
    return [
        {
            "index": d.index,
            "hypothesis_id": d.hypothesis_id,
            "p_value": d.p_value,
            "threshold": d.threshold,
            "rejected": d.rejected,
            "wealth_after": d.wealth_after,
            "tau": d.tau,
            "reason": d.reason,
        }
        for d in decisions
        if d.index in wanted
    ]


def _judgement(stats: DiscoveryStats) -> str:
    if stats.n_rejections == 0:
        return "no rejections: FDP defined as 0 (no discovery, no error)"
    if stats.false_discoveries == 0:
        return (
            f"R={stats.n_rejections} all on non-null hypotheses: V=0, FDP=0"
        )
    return (
        f"R={stats.n_rejections}, V={stats.false_discoveries} rejected null "
        f"hypotheses: FDP={stats.fdp:.4f} for this single replication "
        "(FDR control is an expectation, not a per-run cap)"
    )


def run_experiment(
    config: ExperimentConfig, out_dir: str
) -> dict:
    """Execute all replications and persist JSONL log + JSON summary."""
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, f"{config.name}.jsonl")
    per_run: list[DiscoveryStats] = []
    with open(log_path, "w", encoding="utf-8") as log:
        for run_no in range(1, config.n_replications + 1):
            seed = _seed_for(config.base_seed, run_no)
            stream = config.stream_factory(seed)
            result = run_sequence(
                stream.hypothesis_ids, stream.p_values
            )
            stats = discovery_stats(list(result.decisions), stream.is_null)
            per_run.append(stats)
            record = {
                "experiment": config.name,
                "run_no": run_no,
                "seed": seed,
                "stream_kind": config.stream_kind,
                "stream_params": config.stream_params,
                "n_null": stream.n_null,
                "n_nonnull": stream.n_nonnull,
                "key_states": _key_states(result.decisions),
                "rejection_indicator": result.rejection_indicator(),
                "stats": stats.to_dict(),
                "judgement": _judgement(stats),
            }
            log.write(json.dumps(record, sort_keys=True) + "\n")

    summary = summarize_replications(
        per_run,
        seed=config.base_seed,
        stream_kind=config.stream_kind,
        stream_params=config.stream_params,
        target_fdr=ALPHA,
    )
    summary_path = os.path.join(out_dir, f"{config.name}.summary.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary.to_dict(), fh, indent=2, sort_keys=True)
    return {"summary": summary.to_dict(), "log_path": log_path,
            "summary_path": summary_path}
