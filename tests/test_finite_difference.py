"""Finite-difference validation of scalar-loss gradients.

For every registered case, minigrad's analytic gradients are compared
against central finite differences of an *independent* pure-NumPy loss
(validation_cases.numpy_fn). The suite covers broadcasting, multi-branch
shared nodes, matmul, reductions, activation chains, and empty dims.
"""

import pytest

from minigrad.diagnostics import STATUS_ACCEPTED
from minigrad.finite_difference import central_difference_grad, gradcheck
from minigrad.validation_cases import get_case, list_cases


def test_case_registry_covers_required_scenarios():
    names = list_cases()
    for required in (
        "broadcast_add_mul",
        "shared_multi_branch",
        "matmul_2d",
        "matmul_vector",
        "reduction_mean",
        "reduction_max",
        "activation_chain",
        "empty_dim",
    ):
        assert required in names


@pytest.mark.parametrize("case_name", list_cases())
def test_gradcheck_accepted(case_name):
    report = gradcheck(get_case(case_name))
    details = "; ".join(
        f"{p.name}: ratio={p.max_error_ratio:.3g} ({p.status})"
        for p in report.params
    )
    assert report.status == STATUS_ACCEPTED, f"{case_name}: {details}"


def test_central_difference_on_empty_tensor_is_vacuous():
    import numpy as np
    grad = central_difference_grad(lambda arr: 0.0, np.zeros((0, 3)), 1e-6)
    assert grad.shape == (0, 3)


def test_central_difference_matches_known_derivative():
    import numpy as np
    x = np.array([1.0, -2.0, 0.5])
    grad = central_difference_grad(lambda v: float((v ** 3).sum()), x, 1e-6)
    np.testing.assert_allclose(grad, 3 * x ** 2, rtol=1e-5, atol=1e-7)
