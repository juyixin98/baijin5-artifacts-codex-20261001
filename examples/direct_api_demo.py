"""Direct library demo: every fixture scenario, normal and abnormal.

Runs without a server. Creates a persisted service, applies the six fixture
scenarios in order, cross-checks each step against the independent dense
reference, and prints the concrete decision basis. The invalid batch is
exercised and its typed failure category is reported explicitly.

Usage::

    python -m examples.direct_api_demo
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from sparse_embeddings.config import (
    ClippingConfig,
    OptimizerConfig,
    ServiceConfig,
    TableConfig,
)
from sparse_embeddings.observability import StructuredLogger
from sparse_embeddings.persistence import CheckpointStore, restore_into
from sparse_embeddings.state import SparseOptimizerService
from sparse_embeddings.tensor_types import (
    ErrorCategory,
    SparseEmbeddingError,
    SparseGradientBatch,
)
from sparse_embeddings.validation import (
    DenseReferenceModel,
    assert_sparse_matches_dense,
    assert_step_matches_reference,
)

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"


def main() -> int:
    payload = json.loads((ROOT / "fixtures" / "small_batches.json").read_text())
    batches = {b["name"]: b for b in payload["batches"]}
    V, D = payload["vocab_size"], payload["dim"]

    runtime = RESULTS_DIR / "demo_runtime"
    cfg = ServiceConfig(
        data_dir=runtime / "data",
        log_path=runtime / "logs" / "demo.jsonl",
        log_to_stderr=False,
    )
    cfg.ensure_dirs()
    logger = StructuredLogger(cfg.log_path, stderr=False)
    store = CheckpointStore(cfg.data_dir / "checkpoints")
    svc = SparseOptimizerService(logger=logger, store=store)

    table_cfg = TableConfig(
        name="demo_embed",
        vocab_size=V,
        dim=D,
        optimizer=OptimizerConfig(name="momentum_sgd", learning_rate=0.01, momentum=0.9),
        clipping=ClippingConfig(mode="global", max_norm=2.0),
        dtype="float64",
        seed=7,
    )
    table = svc.create_table(table_cfg)
    ref = DenseReferenceModel.from_config(table_cfg, initial_weights=table.weights)

    print("=" * 72)
    print("SPARSE EMBEDDING OPTIMIZER - DIRECT API DEMO")
    print(f"vocab={V} dim={D} optimizer=momentum_sgd clip=global@2.0")
    print("=" * 72)

    order = [
        "duplicate_ids",
        "hot_cold_alternating",
        "empty",
        "large_gradients",
        "zero_gradient_touched",
        "out_of_range_invalid",
    ]
    failures: list[str] = []

    for name in order:
        b = batches[name]
        print(f"\n[scenario] {name}  tokens={len(b['indices'])}")
        try:
            batch = SparseGradientBatch.from_lists(
                b["indices"], b["values"], vocab_size=V, expected_dim=D
            )
        except SparseEmbeddingError as exc:
            print(f"  REJECTED category={exc.category.value}: {exc.message}")
            assert exc.category is ErrorCategory.INDEX_OUT_OF_RANGE
            print("  decision: whole batch rejected, state unchanged (expected)")
            continue

        ref_out = ref.apply(batch.indices, batch.values, batch.scale if batch.nnz else 1.0)
        result = svc.apply_batch(table_cfg.name, batch, run_id=f"demo-{name}")
        assert_step_matches_reference(result, ref_out)
        assert_sparse_matches_dense(table, ref)

        print(f"  stepped={result.stepped} reason={result.reason}")
        print(
            f"  global_step {result.global_step_before} -> {result.global_step_after}"
        )
        print(f"  touched={result.touched_indices.tolist()} counts={result.token_counts.tolist()}")
        print(
            f"  pre_norm={result.pre_clip_global_norm:.6f} "
            f"post_norm={result.post_clip_global_norm:.6f} clipped={result.clip_applied}"
        )
        if result.zero_gradient_rows.size:
            print(f"  zero-gradient touched rows={result.zero_gradient_rows.tolist()} "
                  f"(momentum decayed, step counted)")
        print("  verification: MATCHES independent dense reference")

    print("\n" + "-" * 72)
    print("Final state checks")
    print("-" * 72)
    print(f"global_step={table.global_step} (empty batch and rejected batch add no step)")
    print(f"row_steps={table.row_steps.tolist()}")
    cold = np.nonzero(table.row_steps == 0)[0].tolist()
    print(f"untouched rows={cold} (momentum exactly zero, weights at seed init)")
    assert table.global_step == 4, failures.append("global step should be 4")
    assert 6 in cold and table.momentum[6].sum() == 0.0

    # Persistence: checkpoint holds only touched rows; restore reproduces state.
    ckpt = store.path_for(table_cfg.name)
    data = np.load(ckpt)
    print(f"\ncheckpoint: {ckpt.relative_to(ROOT)}")
    print(f"  rows persisted={data['touched_indices'].tolist()} (only touched rows)")
    svc2 = SparseOptimizerService(logger=logger)
    restored = restore_into(svc2, table_cfg.name, store)
    np.testing.assert_array_equal(restored.weights, table.weights)
    np.testing.assert_array_equal(restored.momentum, table.momentum)
    print("  restore: weights and momentum reproduced exactly")

    print(f"\nstructured log: {cfg.log_path.relative_to(ROOT)}")
    print(f"result directory: {runtime.relative_to(ROOT)}")
    if failures:
        print("FAILURES:", failures)
        return 1
    print("\nALL DEMO CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
