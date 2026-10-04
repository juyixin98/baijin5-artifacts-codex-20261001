"""Generate the synthetic fixtures under fixtures/.

The reference answers in these fixtures are HAND-DERIVED (see the comments
next to each matrix) and hardcoded here — they are never produced by the
neighbor-joining core under test. The only computation in this script is
path-sum distance extraction from explicitly defined trees plus seeded
noise, both independent of app/nj.py.

Run: python scripts/generate_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _write(name: str, payload: dict) -> None:
    path = FIXTURES / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def additive_4taxon() -> dict:
    # Reference tree (unrooted): cherry (A,B) at P, cherry (C,D) at Q,
    # internal edge P-Q = 4, limbs A-P=1, B-P=2, C-Q=1, D-Q=3.
    # Distances below are hand-summed path lengths of that tree.
    #
    # Hand-derived NJ run (stable tie rule: lowest (i, j) wins):
    #   round 0: Q(A,B)=Q(C,D)=-30 (tie) -> join A,B; limbs 1 and 2.
    #   round 1: all three Q equal (-16, tie) -> join C,D; limbs 1 and 3.
    #   final edge d=4 -> root midpoint, 2 on each side.
    # Expected Newick (leaf ids L0..L3 = A..D): ((L0:1,L1:2):2,(L2:1,L3:3):2);
    return {
        "description": "Additive 4-taxon matrix; NJ must reconstruct it with zero residual.",
        "labels": ["A", "B", "C", "D"],
        "matrix": [
            [0.0, 3.0, 6.0, 8.0],
            [3.0, 0.0, 7.0, 9.0],
            [6.0, 7.0, 0.0, 4.0],
            [8.0, 9.0, 4.0, 0.0],
        ],
        "expected": {
            "newick": "((L0:1,L1:2):2,(L2:1,L3:3):2);",
            "leaf_map": {"L0": "A", "L1": "B", "L2": "C", "L3": "D"},
            "residual_sum_abs": 0.0,
            "cherries": [["A", "B"], ["C", "D"]],
            "ties": [
                {"round": 0, "chosen": [0, 1], "candidates": [[0, 1], [2, 3]]},
                {"round": 1, "chosen": [2, 3], "candidates": [[2, 3], [2, 4], [3, 4]]},
            ],
        },
    }


def negative_branch_4taxon() -> dict:
    # Hand-derived: round 0 ties Q(A,B)=Q(C,D)=-24 -> join A,B.
    #   limb(A) = 0.5*1 + (5-21)/4 = -3.5  (negative), limb(B) = 4.5.
    # The matrix is additive *with* a negative edge, so 'allow' mode fits
    # exactly (residual 0) while 'clamp' mode distorts 3 pairs by 3.5 each:
    #   clamped sum_abs = 10.5, max_abs = 3.5.
    return {
        "description": "Additive matrix whose NJ reconstruction requires a negative branch.",
        "labels": ["A", "B", "C", "D"],
        "matrix": [
            [0.0, 1.0, 2.0, 2.0],
            [1.0, 0.0, 10.0, 10.0],
            [2.0, 10.0, 0.0, 2.0],
            [2.0, 10.0, 2.0, 0.0],
        ],
        "expected": {
            "allow": {
                "newick": "((L0:-3.5,L1:4.5):2.25,(L2:1,L3:1):2.25);",
                "residual_sum_abs": 0.0,
                "negative_branch": {"round": 0, "node": 0, "length": -3.5},
            },
            "clamp": {
                "residual_sum_abs": 10.5,
                "residual_max_abs": 3.5,
                "clamped": {"round": 0, "node": 0, "raw_length": -3.5},
            },
        },
    }


def noisy_5taxon(seed: int = 20261004, amplitude: float = 0.1) -> dict:
    # Reference tree (unrooted), hand-summed into `base` below:
    #   cherries (A,B) at P, (C,D) at Q; internal R joins P, Q, E.
    #   A-P=1, B-P=2, C-Q=1.5, D-Q=0.5, P-R=2, Q-R=1, E-R=3.
    base = np.array(
        [
            [0.0, 3.0, 5.5, 4.5, 6.0],
            [3.0, 0.0, 6.5, 5.5, 7.0],
            [5.5, 6.5, 0.0, 2.0, 5.5],
            [4.5, 5.5, 2.0, 0.0, 4.5],
            [6.0, 7.0, 5.5, 4.5, 0.0],
        ]
    )
    rng = np.random.default_rng(seed)
    noise = np.zeros_like(base)
    for i in range(5):
        for j in range(i + 1, 5):
            noise[i, j] = noise[j, i] = rng.uniform(-amplitude, amplitude)
    noisy = np.round(base + noise, 6)
    assert (noisy >= 0).all() and np.allclose(np.diag(noisy), 0.0)
    return {
        "description": "Additive 5-taxon matrix plus seeded symmetric noise; "
        "non-additive, so a positive residual is expected and reported.",
        "seed": seed,
        "noise_amplitude": amplitude,
        "labels": ["A", "B", "C", "D", "E"],
        "base_additive_matrix": base.tolist(),
        "matrix": noisy.tolist(),
        "expected": {
            "residual_sum_abs_positive": True,
            "cherries": [["A", "B"], ["C", "D"]],
        },
    }


def duplicate_leaves() -> dict:
    # alpha and beta are identical sequences -> p-distance 0 -> they must
    # form a cherry with zero-length limbs. Hand-derived from the p-distance
    # matrix (d(alpha,beta)=0, d(alpha|beta,gamma)=0.5,
    # d(alpha|beta,delta)=1, d(gamma,delta)=0.5):
    #   round 0: Q(alpha,beta)=Q(gamma,delta)=-3 (tie) -> join alpha,beta
    #            with limbs 0 and 0.
    #   round 1: join gamma,delta with limbs 0 and 0.5.
    #   final edge d=0.5 -> root midpoint, 0.25 on each side.
    #   Residual is exactly 0 (the p-distance matrix here is additive).
    fasta = ">alpha\nAAAA\n>beta\nAAAA\n>gamma\nAACC\n>delta\nCCCC\n"
    return {
        "description": "FASTA with two identical sequences (duplicate leaves).",
        "fasta": fasta,
        "expected": {
            "newick": "((L0:0,L1:0):0.25,(L2:0,L3:0.5):0.25);",
            "leaf_map": {"L0": "alpha", "L1": "beta", "L2": "gamma", "L3": "delta"},
            "residual_sum_abs": 0.0,
            "zero_length_cherry": ["alpha", "beta"],
        },
    }


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    _write("additive_4taxon.json", additive_4taxon())
    _write("negative_branch_4taxon.json", negative_branch_4taxon())
    _write("noisy_5taxon.json", noisy_5taxon())
    _write("duplicate_leaves.json", duplicate_leaves())


if __name__ == "__main__":
    main()
