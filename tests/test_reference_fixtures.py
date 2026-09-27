"""Gradients vs. precomputed reference fixtures.

tests/fixtures/reference_grads.json is produced by
scripts/generate_fixtures.py using *pure NumPy* analytic formulas — the
reference answers are independent of the autodiff engine under test.
"""

import numpy as np
import pytest

from minigrad import Tensor

# minigrad reconstruction of each fixture case's scalar loss
CASE_FNS = {
    "broadcast_sum": lambda t: (t["x"] * t["b"]).sum() + t["b"].sum(),
    "shared_branch": lambda t: (t["x"] * t["x"]).sum() + t["x"].sum(),
    "matmul_2d": lambda t: (t["a"] @ t["b"]).sum(),
    "mean_reduction": lambda t: (t["x"] ** 2).mean(),
}


@pytest.mark.parametrize("case_name", sorted(CASE_FNS))
def test_gradients_match_reference_fixture(reference_grads, case_name):
    case = reference_grads[case_name]
    tensors = {
        name: Tensor(np.asarray(value), requires_grad=True, name=name)
        for name, value in case["params"].items()
    }
    loss = CASE_FNS[case_name](tensors)
    loss.backward()

    np.testing.assert_allclose(
        float(loss.data), case["loss"], rtol=1e-12, atol=1e-12,
        err_msg=f"{case_name}: forward loss mismatch",
    )
    for name, expected in case["grads"].items():
        assert tensors[name].grad is not None, f"{case_name}/{name}: grad is None"
        np.testing.assert_allclose(
            tensors[name].grad, np.asarray(expected), rtol=1e-10, atol=1e-12,
            err_msg=f"{case_name}/{name}: gradient mismatch vs reference fixture",
        )
