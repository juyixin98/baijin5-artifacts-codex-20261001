"""Tests for the independent NumPy oracle, differential verifier and checks.

Key contract: none of the reference answers here or inside
:mod:`tensorcraft.validation.oracle` come from the core under test -- the
oracle imports NumPy only.
"""

from __future__ import annotations

import numpy as np
import pytest

import tensorcraft.validation.oracle as oracle
from tensorcraft.validation import fixtures, run_all_checks, run_scenario
from tensorcraft.validation.checks import (
    check_broadcast_zero_strides,
    check_overlap_write_rejected,
    check_overlap_write_temp_copy,
    check_transpose_then_reshape_decision,
    check_view_aliases_parent,
)


def test_oracle_module_does_not_import_core():
    """Guardrail: the oracle must stay independent of the system under test."""
    import ast
    import pathlib
    source_path = pathlib.Path(oracle.__file__)
    tree = ast.parse(source_path.read_text())
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    assert not any(
        mod == "tensorcraft" or mod.startswith("tensorcraft.")
        for mod in imported_modules), f"oracle imports core: {imported_modules}"


class TestBuiltinScenarios:
    @pytest.mark.parametrize("name", sorted(fixtures.SCENARIOS))
    def test_scenario_has_no_failures(self, name):
        report = run_scenario(fixtures.SCENARIOS[name])
        failures = report.failures
        assert not failures, [
            f"{f.name}: {f.detail}" for f in failures]

    def test_required_scenario_families_present(self):
        names = set(fixtures.SCENARIOS)
        # The four contract-mandated families.
        assert "transpose_reshape_2d" in names
        assert "broadcast_zero_stride" in names
        assert "overlapping_slice" in names
        assert "empty_tensors" in names

    def test_transpose_reshape_records_copy_on_both_sides(self):
        report = run_scenario(fixtures.SCENARIOS["transpose_reshape_2d"])
        by_name = {r.name: r for r in report.results}
        flat = by_name["flat"]
        assert flat.passed
        assert flat.core_facts["copied"] is True
        assert flat.oracle_facts["copied"] is True
        direct = by_name["flat_direct"]
        assert direct.core_facts["copied"] is False
        assert direct.oracle_facts["copied"] is False

    def test_broadcast_scenario_zero_stride_facts(self):
        report = run_scenario(fixtures.SCENARIOS["broadcast_zero_stride"])
        wide = {r.name: r for r in report.results}["wide"]
        assert wide.core_facts["strides"] == [1, 0]
        assert wide.passed

    def test_empty_scenarios_are_reported_as_uncertain_separately(self):
        report = run_scenario(fixtures.SCENARIOS["empty_tensors"])
        # Value checks all pass...
        assert not report.failures
        # ...and the unobservable copy/view distinction is listed apart.
        assert report.uncertainties
        assert all("empty result" in r.detail for r in report.uncertainties)

    def test_failure_categories_asserted_concretely(self):
        report = run_scenario(fixtures.SCENARIOS["failure_categories"])
        by_name = {r.name: r for r in report.results}
        assert by_name["bad_size"].observed_category == "SIZE_MISMATCH"
        assert by_name["oob"].observed_category == "INDEX_OUT_OF_BOUNDS"
        assert by_name["zero_step"].observed_category == "INVALID_INDEX"
        assert by_name["bad_axes"].observed_category == "SHAPE_MISMATCH"

    def test_3d_forced_copy_raises_non_contiguous(self):
        report = run_scenario(fixtures.SCENARIOS["transpose_reshape_3d"])
        by_name = {r.name: r for r in report.results}
        assert by_name["r_never"].observed_category == "NON_CONTIGUOUS_VIEW"
        assert by_name["r"].core_facts["copied"] is True


class TestDirectMemoryChecks:
    def test_every_direct_check_passes(self):
        results = run_all_checks()
        assert len(results) == 7
        for result in results:
            assert result.passed, f"{result.name}: {result.detail}"

    def test_view_alias_check_concrete_facts(self):
        result = check_view_aliases_parent()
        assert result.passed
        assert result.facts["aliases"] is True
        assert result.facts["parent_changed"] is True

    def test_overlap_reject_reports_concrete_offset(self):
        result = check_overlap_write_rejected()
        assert result.passed
        assert result.facts["first_duplicated_offset"] is not None

    def test_temp_copy_explains_numpy_contrast(self):
        result = check_overlap_write_temp_copy()
        assert result.passed
        contrast = result.facts[
            "numpy_overlapping_fill_buffer_for_contrast"]
        # NumPy's own overlapping fill produces a buffer at all (we record
        # it regardless of its value; it documents why the policy exists).
        assert len(contrast) == 5

    def test_broadcast_zero_stride_counts(self):
        result = check_broadcast_zero_strides()
        assert result.passed
        assert result.facts["offset_counts"] == {"0": 4, "1": 4, "2": 4} \
            or result.facts["offset_counts"] == {0: 4, 1: 4, 2: 4}

    def test_transpose_reshape_decision_facts(self):
        result = check_transpose_then_reshape_decision()
        assert result.passed
        assert result.facts["transposed_reshape_copied"] is True
        assert result.facts["contiguous_reshape_is_view"] is True


class TestCustomScenarioFromAPI:
    def test_custom_scenario_runs_on_both_interpreters(self):
        steps = [
            {"op": "input", "name": "a",
             "data": [1, 2, 3, 4], "dtype": "int64"},
            {"op": "slice", "name": "r", "input": "a",
             "index": [[1, 4, 1]]},
            {"op": "scalar", "name": "s", "input": "r",
             "subop": "multiply", "value": 10},
        ]
        report = run_scenario(steps)
        assert not report.failures
        final = {r.name: r for r in report.results}["s"]
        assert final.passed

    def test_unexpected_failure_is_a_failure_not_an_error(self):
        steps = [
            {"op": "input", "name": "a", "data": [1, 2], "dtype": "int64"},
            {"op": "reshape", "name": "bad", "input": "a",
             "shape": [99], "order": "C"},
        ]
        report = run_scenario(steps)
        # No expect_error declared -> recorded as a concrete failed step.
        bad = {r.name: r for r in report.results}["bad"]
        assert not bad.passed
        assert bad.observed_category == "SIZE_MISMATCH"
