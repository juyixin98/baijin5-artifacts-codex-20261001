"""Tests for identifiability assessment.

Reference ranks are analytic facts, not outputs of the code under test:
a single real sinusoid spans a 2-dimensional subspace, so its convolution
matrix has rank exactly 2; white noise is full rank with probability 1.
"""

from __future__ import annotations

import numpy as np

from app.signal_processing.convolution import build_convolution_matrix
from app.signal_processing.identifiability import assess_identifiability

from .fixtures import narrowband_excitation, white_excitation


def test_white_excitation_is_identifiable():
    x = white_excitation(500, seed=3)
    matrix = build_convolution_matrix(x, 8, "valid")
    report = assess_identifiability(matrix)
    assert report.rank == 8
    assert report.identifiable
    assert np.isfinite(report.condition_number)


def test_single_sinusoid_has_rank_two():
    x = narrowband_excitation(500, frequency=0.05, n_tones=1)
    order = 6
    matrix = build_convolution_matrix(x, order, "valid")
    report = assess_identifiability(matrix)
    assert report.rank == 2  # analytic: one real tone spans a 2-D subspace
    assert not report.identifiable
    assert report.condition_number == float("inf") or report.condition_number > 1e10


def test_two_sinusoids_have_rank_four():
    x = narrowband_excitation(500, frequency=0.05, n_tones=2)
    matrix = build_convolution_matrix(x, 6, "valid")
    report = assess_identifiability(matrix)
    assert report.rank == 4
    assert not report.identifiable


def test_rank_never_exceeds_column_count():
    x = white_excitation(300, seed=4)
    matrix = build_convolution_matrix(x, 10, "valid")
    report = assess_identifiability(matrix)
    assert report.rank <= report.n_columns == 10
    assert len(report.singular_values) == 10
    # singular values are sorted in descending order
    sv = np.asarray(report.singular_values)
    assert np.all(np.diff(sv) <= 0)


def test_tolerance_is_reported_and_positive():
    x = white_excitation(200, seed=5)
    matrix = build_convolution_matrix(x, 4, "valid")
    report = assess_identifiability(matrix)
    assert report.tolerance > 0.0
