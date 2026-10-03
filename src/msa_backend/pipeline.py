"""Run pipeline: parse -> weight -> column statistics -> consensus -> persist.

Each step logs its progress with the run id so server/test logs can be
correlated to a specific input. Failures mark the run ``failed`` in the
provenance store with a typed error category and then propagate -- the
pipeline never converts an exception into a successful result.
"""

from __future__ import annotations

import hashlib
import platform
import uuid
from datetime import datetime, timezone

import numpy as np

from . import __version__
from .config import AppConfig
from .domain.columns import column_distribution
from .domain.consensus import call_consensus
from .domain.coordinates import build_coordinate_maps
from .domain.entropy import information_content_bits, shannon_entropy_bits
from .domain.types import AlignmentResult, ColumnResult
from .domain.weights import compute_weights
from .errors import MsaBackendError
from .logging_utils import get_logger
from .parsing.fasta import Alignment, parse_fasta_alignment
from .provenance.db import ProvenanceStore

logger = get_logger("pipeline")


def component_versions() -> dict:
    return {
        "msa_backend": __version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
    }


def compute_alignment(alignment: Alignment, config: AppConfig) -> AlignmentResult:
    """Pure computation: alignment + config -> full result (no I/O)."""
    algo = config.algorithm
    weights_by_id, clusters, weight_vector = compute_weights(
        alignment, algo.identity_threshold, algo.gap_symbol
    )
    total_weight = float(weight_vector.sum())

    columns: list[ColumnResult] = []
    for col_idx in range(alignment.n_columns):
        distribution, coverage, gap_fraction = column_distribution(
            alignment, weight_vector, algo.alphabet, algo.gap_symbol, col_idx
        )
        entropy = shannon_entropy_bits(distribution)
        information = information_content_bits(distribution, len(algo.alphabet))
        consensus, status = call_consensus(
            distribution,
            coverage,
            algo.conservation_threshold,
            algo.min_effective_coverage,
        )
        columns.append(
            ColumnResult(
                column_index=col_idx + 1,
                distribution=distribution,
                entropy_bits=entropy,
                information_content_bits=information,
                effective_coverage=coverage,
                gap_fraction=gap_fraction,
                consensus=consensus,
                status=status,
            )
        )

    return AlignmentResult(
        sequence_ids=alignment.sequence_ids,
        weights=weights_by_id,
        clusters=clusters,
        total_weight=total_weight,
        columns=tuple(columns),
        coordinate_maps=build_coordinate_maps(alignment, algo.gap_symbol),
    )


def run_pipeline(
    fasta_text: str,
    label: str,
    config: AppConfig,
    store: ProvenanceStore,
) -> tuple[str, AlignmentResult]:
    """Execute a full run, persisting provenance at each stage."""
    run_id = uuid.uuid4().hex[:12]
    input_sha256 = hashlib.sha256(fasta_text.encode("utf-8")).hexdigest()
    started = datetime.now(timezone.utc).isoformat()

    store.begin_run(
        run_id=run_id,
        label=label,
        created_at=started,
        input_sha256=input_sha256,
        config_snapshot=config.algorithm.snapshot(),
        versions=component_versions(),
    )
    logger.info(
        "run=%s step=begin label=%r input_sha256=%s versions=%s",
        run_id, label, input_sha256[:12], component_versions(),
    )

    try:
        alignment = parse_fasta_alignment(fasta_text, config.algorithm.gap_symbol)
        logger.info(
            "run=%s step=parse sequences=%d columns=%d",
            run_id, alignment.n_sequences, alignment.n_columns,
        )

        result = compute_alignment(alignment, config)
        logger.info(
            "run=%s step=weights clusters=%d total_weight=%.4f weights=%s",
            run_id, len(result.clusters), result.total_weight,
            {k: round(v, 6) for k, v in result.weights.items()},
        )
        for column in result.columns:
            logger.info(
                "run=%s step=column idx=%d coverage=%.4f gap_fraction=%.4f"
                " entropy=%.6f ic=%.6f consensus=%s status=%s",
                run_id, column.column_index, column.effective_coverage,
                column.gap_fraction, column.entropy_bits,
                column.information_content_bits, column.consensus, column.status,
            )

        for idx, column in enumerate(result.columns):
            store.insert_column(run_id, column)
            if (idx + 1) % 50 == 0:
                logger.info("run=%s step=persist progress=%d/%d columns",
                            run_id, idx + 1, len(result.columns))
        for seq_id, mapping in result.coordinate_maps.items():
            store.insert_coordinate_map(run_id, seq_id, mapping)
        for cluster_id, cluster in enumerate(result.clusters):
            for seq_id in cluster:
                store.insert_weight(run_id, seq_id, result.weights[seq_id], cluster_id)
        store.commit()

        finished = datetime.now(timezone.utc).isoformat()
        store.complete_run(
            run_id, finished, alignment.n_sequences, alignment.n_columns,
            result.total_weight,
        )
        logger.info("run=%s step=complete status=completed", run_id)
        return run_id, result

    except MsaBackendError as exc:
        finished = datetime.now(timezone.utc).isoformat()
        store.fail_run(run_id, finished, exc.category, str(exc))
        logger.warning(
            "run=%s step=failed category=%s message=%s", run_id, exc.category, exc
        )
        raise
    except Exception as exc:  # unknown failure: record, never fake success
        finished = datetime.now(timezone.utc).isoformat()
        store.fail_run(run_id, finished, "internal_error", str(exc))
        logger.exception("run=%s step=failed category=internal_error", run_id)
        raise
