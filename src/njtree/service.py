"""Orchestration boundary: input -> validated run -> provenance -> result.

The service owns run lifecycle and logging. Validation errors happen *before*
a run is created (invalid input never occupies a run_id); computation
failures happen *inside* a run and are recorded with their error category so
the failing run can be inspected and replayed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict

from .errors import NJError, StateConflictError, UnknownRunError
from .matrix import validate_distance_matrix
from .models import (
    BuildParams,
    BuildResult,
    DistanceMatrix,
    NegativeBranchMode,
    ReplayReport,
)
from .nj import neighbor_joining
from .parsing import hamming_matrix, parse_fasta
from .provenance import ProvenanceStore
from .residuals import compute_residuals
from .tree import leaf_map, patristic_distances, to_newick


class TreeService:
    def __init__(self, store: ProvenanceStore | None = None,
                 logger: logging.Logger | None = None) -> None:
        self.store = store or ProvenanceStore(":memory:")
        self.log = logger or logging.getLogger("njtree.service")

    # -- input adapters -------------------------------------------------------

    def build_from_matrix(self, labels, values, params: BuildParams) -> BuildResult:
        dm = validate_distance_matrix(labels, values, max_taxa=params.max_taxa)
        return self.build(dm, params)

    def build_from_fasta(self, text: str, params: BuildParams) -> BuildResult:
        records = parse_fasta(text)
        dm = hamming_matrix(records, max_taxa=params.max_taxa)
        return self.build(dm, params)

    # -- core lifecycle ---------------------------------------------------------

    def build(self, dm: DistanceMatrix, params: BuildParams) -> BuildResult:
        run_id = params.run_id or uuid.uuid4().hex
        input_json = json.dumps(
            {"labels": list(dm.labels), "values": dm.values.tolist()}, sort_keys=True)
        digest = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
        params_json = json.dumps({
            "negative_branch_mode": params.negative_branch_mode.value,
            "max_taxa": params.max_taxa,
        }, sort_keys=True)

        outcome = self.store.create_run(run_id, digest, input_json, params_json, dm.n)
        if outcome == "exists_same":
            run = self.store.get_run(run_id)
            result = self.store.get_result(run_id)
            if run is not None and run["status"] == "completed" and result is not None:
                self.log.info("run=%s idempotent hit: returning stored result", run_id)
                return BuildResult(
                    run_id=run_id,
                    newick=result["newick"],
                    leaf_map=result["leaf_map"],
                    residuals=_residuals_from_json(result["residuals"]),
                    steps=[],  # steps are available via the provenance endpoint
                    negative_events=[],
                    params=params,
                    idempotent=True,
                )
            # Same input but the previous attempt did not complete: the run_id
            # stays owned by that attempt to keep provenance append-only.
            raise StateConflictError(
                f"run_id {run_id!r} exists with identical input but status "
                f"{run['status']!r}; refusing to overwrite provenance",
                run_id=run_id)

        self.log.info("run=%s start n_taxa=%d mode=%s input_sha256=%s",
                      run_id, dm.n, params.negative_branch_mode.value, digest)
        try:
            root, steps, events = neighbor_joining(
                dm, mode=params.negative_branch_mode, run_id=run_id, logger=self.log)
        except NJError as exc:
            self.store.finish_run(run_id, status="failed",
                                  error_category=exc.category.value, error_message=exc.message)
            self.log.error("run=%s failed category=%s message=%s",
                           run_id, exc.category.value, exc.message)
            raise

        newick = to_newick(root)
        lmap = leaf_map(root)
        residuals = compute_residuals(dm, patristic_distances(root))
        for step in steps:
            self.store.record_step(run_id, step.step_index, asdict(step))
        residuals_json = _residuals_to_json(residuals)
        self.store.record_result(
            run_id, newick=newick, leaf_map=lmap,
            residuals=residuals_json, negative_events=[asdict(e) for e in events])
        self.store.finish_run(run_id, status="completed")
        self.log.info(
            "run=%s completed steps=%d total_residual=%.10g max_residual=%.10g negative_events=%d",
            run_id, len(steps), residuals.total_absolute, residuals.max_absolute, len(events))
        return BuildResult(
            run_id=run_id, newick=newick, leaf_map=lmap, residuals=residuals,
            steps=steps, negative_events=events, params=params)

    # -- replay -----------------------------------------------------------------

    def replay(self, run_id: str) -> ReplayReport:
        run = self.store.get_run(run_id)
        if run is None:
            raise UnknownRunError(f"unknown run_id {run_id!r}", run_id=run_id)
        if run["status"] != "completed":
            return ReplayReport(run_id, match=False,
                                differences=[f"run status is {run['status']!r}, nothing to replay"])
        payload = json.loads(run["input_json"])
        params_payload = json.loads(run["params_json"])
        dm = validate_distance_matrix(payload["labels"], payload["values"])
        mode = NegativeBranchMode(params_payload["negative_branch_mode"])
        root, steps, _ = neighbor_joining(dm, mode=mode, run_id=run_id, logger=self.log)

        differences: list[str] = []
        stored = self.store.get_result(run_id)
        if stored is None or stored["newick"] != to_newick(root):
            differences.append("newick mismatch between replay and stored result")
        stored_steps = self.store.get_steps(run_id)
        replayed = [asdict(s) for s in steps]
        canonical = lambda s: json.dumps(s, sort_keys=True)
        if [canonical(s) for s in replayed] != [canonical(s) for s in stored_steps]:
            differences.append("join-step sequence mismatch between replay and provenance")
        match = not differences
        self.log.info("run=%s replay match=%s differences=%d", run_id, match, len(differences))
        return ReplayReport(run_id, match=match, differences=differences)


def _residuals_to_json(residuals) -> dict:
    return {
        "total_absolute": residuals.total_absolute,
        "max_absolute": residuals.max_absolute,
        "rmse": residuals.rmse,
        "pairs": [asdict(p) for p in residuals.pairs],
    }


def _residuals_from_json(payload: dict):
    from .models import PairResidual, ResidualReport
    return ResidualReport(
        pairs=[PairResidual(**p) for p in payload["pairs"]],
        total_absolute=payload["total_absolute"],
        max_absolute=payload["max_absolute"],
        rmse=payload["rmse"],
    )
