"""Orchestration: parse -> classify -> estimate -> bootstrap -> persist.

This module owns the data contract between the API layer, the domain
algorithms, and the provenance store. It translates domain outcomes into
the response document and guarantees every run (including failed ones
that got far enough to have a run_id) leaves an auditable record.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

from .bootstrap import BootstrapResult, bootstrap_ci
from .errors import InputValidationError
from .models import Model, count_sites, estimate_distance, site_outcomes
from .provenance import RunStore
from .sequences import AlignedDataset, parse_fasta, validate_alignment


def canonical_request_hash(payload: dict[str, Any]) -> str:
    """Stable hash of the request payload for idempotency checks."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def dataset_from_payload(payload: dict[str, Any]) -> AlignedDataset:
    """Build a validated dataset from either a `sequences` list or `fasta` text."""
    has_list = payload.get("sequences") is not None
    has_fasta = payload.get("fasta") is not None
    if has_list == has_fasta:
        raise InputValidationError(
            "ambiguous_input",
            "provide exactly one of 'sequences' (list of {id, sequence}) or 'fasta' (text)",
        )
    if has_fasta:
        return parse_fasta(str(payload["fasta"]))
    records = payload["sequences"]
    ids = [str(r.get("id", "")) for r in records]
    seqs = [str(r.get("sequence", "")) for r in records]
    return validate_alignment(ids, seqs)


def compute_distances(payload: dict[str, Any], store: RunStore) -> dict[str, Any]:
    """Run the full pipeline and persist provenance. Returns the response doc."""
    dataset = dataset_from_payload(payload)
    model = Model(payload["model"])
    boot_spec = payload.get("bootstrap")

    request_hash = canonical_request_hash(payload)
    run_id = payload.get("run_id") or store.new_run_id()

    seed = boot_spec.get("seed") if boot_spec else None
    store.create_run(
        run_id=run_id,
        request_hash=request_hash,
        request_json=json.dumps(payload, sort_keys=True),
        model=model.value,
        seed=seed,
        status="completed",
    )

    # Idempotent replay: run already fully recorded -> return stored results.
    existing = store.get_run(run_id)
    if existing is not None and existing.pair_results:
        return _response_from_stored(existing)

    results: list[dict[str, Any]] = []
    for idx_a, idx_b in itertools.combinations(range(len(dataset.ids)), 2):
        id_a, id_b = dataset.ids[idx_a], dataset.ids[idx_b]
        seq_a, seq_b = dataset.sequences[idx_a], dataset.sequences[idx_b]

        outcomes = site_outcomes(seq_a, seq_b)
        counts = count_sites(seq_a, seq_b)
        est = estimate_distance(counts, model)

        boot: BootstrapResult | None = None
        if boot_spec is not None:
            boot = bootstrap_ci(
                outcomes,
                model,
                replicates=int(boot_spec["replicates"]),
                confidence=float(boot_spec["confidence"]),
                seed=int(boot_spec["seed"]),
            )

        pair_doc: dict[str, Any] = {
            "seq_a": id_a,
            "seq_b": id_b,
            "n_valid_sites": counts.n_valid,
            "n_match": counts.n_match,
            "n_transition": counts.n_transition,
            "n_transversion": counts.n_transversion,
            "p_distance": est.p_distance,
            "model": model.value,
            "distance": est.distance,
            "status": est.status.value,
            "rationale": list(est.rationale),
            "bootstrap": _bootstrap_doc(boot),
        }
        store.record_pair_result(
            run_id=run_id,
            seq_a=id_a,
            seq_b=id_b,
            counts=(counts.n_valid, counts.n_match, counts.n_transition, counts.n_transversion),
            p_distance=est.p_distance,
            distance=est.distance,
            status=est.status.value,
            rationale=list(est.rationale),
            bootstrap=pair_doc["bootstrap"],
        )
        results.append(pair_doc)

    return {
        "run_id": run_id,
        "model": model.value,
        "status": "completed",
        "n_sequences": len(dataset.ids),
        "alignment_length": dataset.length,
        "results": results,
    }


def _bootstrap_doc(boot: BootstrapResult | None) -> dict[str, Any] | None:
    if boot is None:
        return None
    return {
        "status": boot.status.value,
        "ci_low": boot.ci_low,
        "ci_high": boot.ci_high,
        "confidence": boot.confidence,
        "replicates": boot.replicates,
        "n_saturated": boot.n_saturated,
        "seed": boot.seed,
        "method": "site resampling with replacement, percentile interval, PCG64",
    }


def _response_from_stored(stored) -> dict[str, Any]:
    results = []
    for row in stored.pair_results:
        results.append({
            "seq_a": row["seq_a"],
            "seq_b": row["seq_b"],
            "n_valid_sites": row["n_valid_sites"],
            "n_match": row["n_match"],
            "n_transition": row["n_transition"],
            "n_transversion": row["n_transversion"],
            "p_distance": row["p_distance"],
            "model": stored.model,
            "distance": row["distance"],
            "status": row["status"],
            "rationale": json.loads(row["rationale_json"]),
            "bootstrap": json.loads(row["bootstrap_json"]) if row["bootstrap_json"] else None,
        })
    request = json.loads(stored.request_json)
    return {
        "run_id": stored.run_id,
        "model": stored.model,
        "status": stored.status,
        "n_sequences": len(request.get("sequences", [])) or None,
        "alignment_length": None,
        "results": results,
        "replayed": True,
    }
