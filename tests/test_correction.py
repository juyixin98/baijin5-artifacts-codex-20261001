"""Multiple-testing correction against hand-computed reference values."""
import pytest

from app.correction import benjamini_hochberg, bonferroni
from tests.reference_data import CORR_BH, CORR_BONFERRONI, CORR_PVALUES


def test_bonferroni_matches_reference():
    assert bonferroni(CORR_PVALUES) == pytest.approx(CORR_BONFERRONI)


def test_benjamini_hochberg_matches_reference():
    assert benjamini_hochberg(CORR_PVALUES) == pytest.approx(CORR_BH)


def test_bonferroni_caps_at_one():
    assert bonferroni([0.5, 0.5]) == [1.0, 1.0]


def test_bh_caps_at_one_and_preserves_order():
    adjusted = benjamini_hochberg([0.9, 0.01])
    assert adjusted[1] == pytest.approx(0.02)  # 0.01 * 2 / 1
    assert adjusted[0] == pytest.approx(0.9)   # 0.9 * 2 / 2


def test_empty_input():
    assert bonferroni([]) == []
    assert benjamini_hochberg([]) == []
