"""Contract validation tests: each invalid input must fail with the
documented category, not merely 'raise somewhere'."""

import pytest

from edt_service.contracts import EdtRequest
from edt_service.errors import (
    ErrorCategory,
    InvalidGridError,
    InvalidOptionError,
    InvalidSpacingError,
)


def _expect(category, **kwargs):
    with pytest.raises(Exception) as excinfo:
        EdtRequest(**kwargs)
    assert getattr(excinfo.value, "category", None) == category, excinfo.value


def test_valid_minimal_request():
    req = EdtRequest(grid=[[1]])
    assert req.spacing == (1.0, 1.0)
    assert req.tile_size is None


def test_empty_grid_rejected():
    _expect(ErrorCategory.INVALID_GRID, grid=[])


def test_empty_row_rejected():
    _expect(ErrorCategory.INVALID_GRID, grid=[[]])


def test_ragged_grid_rejected():
    _expect(ErrorCategory.INVALID_GRID, grid=[[1, 0], [1]])


def test_non_binary_value_rejected():
    _expect(ErrorCategory.INVALID_GRID, grid=[[2]])


def test_non_integer_value_rejected():
    _expect(ErrorCategory.INVALID_GRID, grid=[[0.5]])


def test_bool_value_rejected():
    _expect(ErrorCategory.INVALID_GRID, grid=[[True]])


def test_zero_spacing_rejected():
    _expect(ErrorCategory.INVALID_SPACING, grid=[[1]], spacing=[0.0, 1.0])


def test_negative_spacing_rejected():
    _expect(ErrorCategory.INVALID_SPACING, grid=[[1]], spacing=[1.0, -2.0])


def test_nonfinite_spacing_rejected():
    _expect(ErrorCategory.INVALID_SPACING, grid=[[1]], spacing=[1.0, float("inf")])


def test_bad_tile_size_rejected():
    _expect(ErrorCategory.INVALID_OPTION, grid=[[1]], tile_size=0)


def test_anisotropic_spacing_accepted():
    req = EdtRequest(grid=[[1]], spacing=[2.5, 0.25])
    assert req.spacing == (2.5, 0.25)


def test_error_types_are_distinct():
    # Failure categories must be distinguishable programmatically.
    with pytest.raises(InvalidGridError):
        EdtRequest(grid=[[1, 2]])
    with pytest.raises(InvalidSpacingError):
        EdtRequest(grid=[[1]], spacing=[-1, 1])
    with pytest.raises(InvalidOptionError):
        EdtRequest(grid=[[1]], tile_size=-3)
