"""Hand-computed reference cases: expected values derived on paper, not by
the implementation under test.

Case derivations (metric |a_i - b_j|, steps {(1,1),(2,1),(1,2)}, cost =
sum of local distances over path cells, denominator n + m):

* a=[0,1], b=[0,2]: distance matrix [[0,2],[1,1]]. Only legal path to
  (1,1) is the diagonal step: cost 0 + 1 = 1, normalized 1/4 = 0.25.
* a=[0,1,2], b=[0,2]: distances row-wise [0,2],[1,1],[2,0]. Cell (0,1)
  is unreachable (first step must leave (0,0) forward). Optimal path is
  (0,0) -> (2,1) via one (2,1) step: cost 0 + 0 = 0, stretch 2.0.
* a=[0,2], b=[0,1]: distances [[0,1],[2,1]]; diagonal-only path, cost 1.
"""

import numpy as np
import pytest

from dtw_service.constraints import SakoeChibaWindow
from dtw_service.dense import dense_dtw
from dtw_service.stretch import step_stretches


def test_hand_case_diagonal_only():
    res = dense_dtw(np.array([0.0, 1.0]), np.array([0.0, 2.0]),
                    SakoeChibaWindow(radius=1))
    assert res.cost == pytest.approx(1.0)
    assert res.path == [(0, 0), (1, 1)]
    assert res.cost / (2 + 2) == pytest.approx(0.25)


def test_hand_case_compression_step():
    res = dense_dtw(np.array([0.0, 1.0, 2.0]), np.array([0.0, 2.0]),
                    SakoeChibaWindow(radius=2))
    assert res.cost == pytest.approx(0.0)
    assert res.path == [(0, 0), (2, 1)]
    assert step_stretches(res.path) == [2.0]


def test_hand_case_second_diagonal():
    res = dense_dtw(np.array([0.0, 2.0]), np.array([0.0, 1.0]),
                    SakoeChibaWindow(radius=1))
    assert res.cost == pytest.approx(1.0)
    assert res.path == [(0, 0), (1, 1)]


def test_identical_sequences_zero_cost():
    res = dense_dtw(np.zeros(6), np.zeros(6), SakoeChibaWindow(radius=2))
    assert res.cost == pytest.approx(0.0)
    assert res.path == [(i, i) for i in range(6)]
