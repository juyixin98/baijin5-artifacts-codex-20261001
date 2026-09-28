"""HTTP client example: normal and abnormal calls with typed verdicts.

Start the server first in another terminal::

    python -m sparse_embeddings --host 127.0.0.1 --port 8000

then run::

    python -m examples.http_client_example
"""

from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("SPARSE_EMBEDDINGS_BASE_URL", "http://127.0.0.1:8000")


def show(title: str, resp: httpx.Response) -> None:
    print(f"\n--- {title} [{resp.status_code}] ---")
    body = resp.json()
    if body.get("ok"):
        r = body.get("result", body)
        if "stepped" in r:
            print(
                f"stepped={r['stepped']} reason={r['reason']} "
                f"step={r['global_step_after']} touched={r['touched_indices']} "
                f"pre_norm={r['pre_clip_global_norm']:.6f} clipped={r['clip_applied']}"
            )
        else:
            print(body)
    else:
        err = body["error"]
        print(f"ok=False category={err['category']}")
        print(f"message={err['message']}")
        print(f"details={err['details']}")


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=10.0) as http:
        try:
            show("health", http.get("/health"))
        except httpx.ConnectError:
            print(f"could not connect to {BASE}; start the server first", file=sys.stderr)
            return 2

        show(
            "create table (global clipping @2.0)",
            http.post(
                "/tables",
                json={
                    "name": "embed",
                    "vocab_size": 8,
                    "dim": 4,
                    "optimizer": {"name": "momentum_sgd", "learning_rate": 0.01},
                    "clipping": {"mode": "global", "max_norm": 2.0},
                },
            ),
        )

        show(
            "duplicate ids aggregated before step",
            http.post(
                "/tables/embed/batches",
                json={
                    "indices": [3, 3, 1, 3, 1],
                    "values": [
                        [-1.02, -0.22, -0.20, 0.21],
                        [-0.13, -0.21, -1.09, 0.33],
                        [-0.08, 1.22, -0.40, 0.52],
                        [1.27, -0.39, 0.14, 0.16],
                        [-0.27, 0.15, 0.63, -0.44],
                    ],
                    "run_id": "example-dup",
                },
            ),
        )

        show(
            "empty batch -> no step, not an error",
            http.post(
                "/tables/embed/batches",
                json={"indices": [], "values": [], "run_id": "example-empty"},
            ),
        )

        show(
            "large gradients -> clipped, step still applied",
            http.post(
                "/tables/embed/batches",
                json={
                    "indices": [2, 5, 2],
                    "values": [
                        [18.0, -14.0, 9.0, -22.0],
                        [-12.0, 19.0, 25.0, 8.0],
                        [7.0, -9.0, 13.0, 16.0],
                    ],
                    "run_id": "example-large",
                },
            ),
        )

        show(
            "ABNORMAL: out-of-range index -> whole batch rejected (400)",
            http.post(
                "/tables/embed/batches",
                json={
                    "indices": [1, 8, 2],
                    "values": [[0.1, 0, 0, 0]] * 3,
                    "run_id": "example-oob",
                },
            ),
        )

        show(
            "ABNORMAL: shape mismatch -> validation_error (422)",
            http.post(
                "/tables/embed/batches",
                json={"indices": [0, 1], "values": [[0.0, 0, 0, 0]]},
            ),
        )

        show(
            "optimizer state of touched row 2 and untouched row 6",
            http.post("/tables/embed/rows/query", json={"indices": [2, 6]}),
        )

    print("\nExample sequence complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
