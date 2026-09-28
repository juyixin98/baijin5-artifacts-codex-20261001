"""Generate deterministic minimal synthetic fixtures.

Run::

    python -m fixtures.generate_fixtures

Produces small JSON files under ``fixtures/`` used by examples and tests:

* ``small_batches.json`` - a dense-small-vocabulary scenario (V=8, D=4) with
  duplicate ids, hot/cold alternating rows, an explicit empty batch and a
  large-gradient batch.

Everything is local and synthetic; no accounts or real data.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIXTURE_DIR = Path(__file__).resolve().parent


def build() -> dict:
    rng = np.random.default_rng(20260927)
    vocab_size, dim = 8, 4

    def grads(n: int, scale: float = 0.5) -> list[list[float]]:
        return np.round(rng.normal(0.0, scale, size=(n, dim)), 6).tolist()

    # Batch 1: duplicate ids must be aggregated before the step.
    b1_idx = [3, 3, 1, 3, 1]
    b1_val = grads(5)

    # Batch 2: hot/cold alternation - rows 0 and 7 only, leaving 1..6 cold.
    b2_idx = [7, 0, 7, 0, 7]
    b2_val = grads(5)

    # Batch 3: explicitly empty.
    b3_idx: list[int] = []
    b3_val: list[list[float]] = []

    # Batch 4: large gradients designed to trigger clipping at max_norm=2.0.
    b4_idx = [2, 5, 2]
    big = np.round(rng.normal(0.0, 20.0, size=(3, dim)), 6).tolist()
    b4_val = big

    # Batch 5: a touched row whose contributions cancel exactly (zero grad),
    # plus a cold row, to exercise the zero-gradient step rule.
    b5_idx = [4, 4]
    v = np.round(rng.normal(0.0, 0.4, size=(1, dim)), 6).tolist()[0]
    b5_val = [v, [-x for x in v]]

    # Batch 6: invalid on purpose - one out-of-range id must reject the WHOLE
    # batch. Consumers that only want valid batches should skip this file key.
    b6_idx = [1, vocab_size, 2]  # vocab_size is illegal (ids go 0..V-1)
    b6_val = grads(3)

    return {
        "schema_version": 1,
        "seed": 20260927,
        "vocab_size": vocab_size,
        "dim": dim,
        "batches": [
            {"name": "duplicate_ids", "indices": b1_idx, "values": b1_val},
            {"name": "hot_cold_alternating", "indices": b2_idx, "values": b2_val},
            {"name": "empty", "indices": b3_idx, "values": b3_val},
            {"name": "large_gradients", "indices": b4_idx, "values": b4_val},
            {"name": "zero_gradient_touched", "indices": b5_idx, "values": b5_val},
            {"name": "out_of_range_invalid", "indices": b6_idx, "values": b6_val},
        ],
    }


def main() -> None:
    payload = build()
    out = FIXTURE_DIR / "small_batches.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(payload['batches'])} batches, V={payload['vocab_size']})")


if __name__ == "__main__":
    main()
