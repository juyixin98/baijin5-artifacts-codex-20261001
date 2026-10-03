"""Integration test: brute-force enumeration vs the solver.

For small random specs, every labeling consistent with the hard seeds is
enumerated and its energy computed by a *test-side* evaluator written in
plain Python (no graphcut.energy import), so the reference optimum is
independent of the implementation under test.
"""

import itertools

import numpy as np
import pytest

from graphcut.contracts import Seed, validate_pairwise
from graphcut.service import run_segmentation


def plain_neighbors(height, width):
    """4-neighbour edges generated in the test, not imported from the core."""
    for r in range(height):
        for c in range(width):
            p = r * width + c
            if c + 1 < width:
                yield p, p + 1
            if r + 1 < height:
                yield p, p + width


def plain_energy(height, width, unary0, unary1, pairwise, labels_flat):
    """Plain-Python energy, independent of graphcut.energy."""
    u0 = [float(x) for row in unary0 for x in row]
    u1 = [float(x) for row in unary1 for x in row]
    total = 0.0
    for i, lab in enumerate(labels_flat):
        total += u1[i] if lab == 1 else u0[i]
    v00, v01, v10, v11 = pairwise
    for p, q in plain_neighbors(height, width):
        lp, lq = labels_flat[p], labels_flat[q]
        if (lp, lq) == (0, 0):
            total += v00
        elif (lp, lq) == (0, 1):
            total += v01
        elif (lp, lq) == (1, 0):
            total += v10
        else:
            total += v11
    return total


def brute_force_min(height, width, unary0, unary1, pairwise, seeds):
    n = height * width
    forced = {r * width + c: lab for r, c, lab in seeds}
    best = float("inf")
    best_labeling = None
    for bits in itertools.product((0, 1), repeat=n):
        if any(bits[i] != lab for i, lab in forced.items()):
            continue
        e = plain_energy(height, width, unary0, unary1, pairwise, bits)
        if e < best - 1e-12:
            best, best_labeling = e, bits
    return best, best_labeling


def random_submodular_table(rng):
    b, c = rng.uniform(0.0, 3.0, size=2)
    a = rng.uniform(0.0, b + c)
    d = rng.uniform(0.0, b + c - a)
    return float(a), float(b), float(c), float(d)


@pytest.mark.parametrize("height,width", [(2, 2), (3, 2), (3, 3), (2, 4)])
def test_solver_matches_brute_force(spec_factory, seeded_rng, test_log,
                                    height, width):
    rng = seeded_rng
    trials = 12
    for trial in range(trials):
        table = random_submodular_table(rng)
        unary0 = rng.uniform(0.0, 4.0, size=(height, width))
        unary1 = rng.uniform(0.0, 4.0, size=(height, width))
        n_seeds = int(rng.integers(0, 3))
        cells = rng.choice(height * width, size=min(n_seeds, height * width),
                           replace=False)
        seeds = [Seed(int(c) // width, int(c) % width, int(rng.integers(0, 2)))
                 for c in cells]

        spec = spec_factory(
            height=height, width=width,
            unary0=unary0, unary1=unary1,
            pairwise=validate_pairwise(*table),
            seeds=seeds,
        )
        result = run_segmentation(spec)
        ref_min, ref_labeling = brute_force_min(
            height, width, unary0, unary1, table,
            [(s.row, s.col, s.label) for s in seeds],
        )
        solver_energy = result.certificate.energy.total
        gap = abs(solver_energy - ref_min)
        test_log.info(
            "enum.trial shape=(%d,%d) trial=%d run_id=%s table=%s "
            "seeds=%s solver=%.9f brute=%.9f gap=%.3e verdict=%s",
            height, width, trial, result.run_id,
            [round(v, 4) for v in table],
            [(s.row, s.col, s.label) for s in seeds],
            solver_energy, ref_min, gap,
            "ok" if gap < 1e-6 else "MISMATCH",
        )
        assert solver_energy == pytest.approx(ref_min, abs=1e-6), (
            f"solver energy {solver_energy} != brute-force optimum {ref_min} "
            f"(run_id={result.run_id}, table={table}, seeds={seeds})"
        )
        # The returned labeling must itself attain the reported energy and
        # respect the seeds.
        labels_flat = tuple(int(v) for v in result.labels.ravel())
        recheck = plain_energy(height, width, unary0, unary1, table,
                                   labels_flat)
        assert recheck == pytest.approx(solver_energy, abs=1e-6)
        for s in seeds:
            assert int(result.labels[s.row, s.col]) == s.label
        # Flow value plus the graph constant must equal the optimum too.
        assert result.certificate.flow_value + result.graph_constant == \
            pytest.approx(ref_min, abs=1e-6)
