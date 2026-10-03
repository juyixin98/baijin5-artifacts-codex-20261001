"""Acceptance suite: tiled jobs vs direct full-image convolution, pixel-exact.

Covers the acceptance matrix: impulse / edge-valued / random / ramp images,
irregular tile sizes, large and even kernels, all three boundary modes,
separable filtering, and interrupt/resume equivalence. The reference is
scipy.ndimage (native boundary modes) via ``kernel.direct_reference`` — an
implementation independent of the tiled engine.
"""

from __future__ import annotations

import numpy as np
import pytest

from tileconv.contract import BoundaryMode, KernelSpec
from tileconv.errors import InjectedInterrupt
from tileconv.fixtures import make_kernel_weights, synthesize
from tileconv.job import TiledJob
from tileconv.kernel import direct_reference
from tileconv.validation import compare_arrays, validate_output

MODES = [BoundaryMode.MIRROR, BoundaryMode.CONSTANT, BoundaryMode.PERIODIC]
ATOL = 1e-9


def run_tiled(store, settings, img, kernel, boundary, tile_shape, cval=0.0,
              interrupt_at=None):
    spec = store.create_image(img, meta={"source": "test"})
    job = TiledJob.create(store, settings.jobs_dir, spec, kernel, boundary,
                          cval=cval, tile_shape=tile_shape,
                          logs_dir=settings.logs_dir)
    if interrupt_at is not None:
        with pytest.raises(InjectedInterrupt):
            job.run(fail_after=interrupt_at)
        job = TiledJob(job.state_path, store)
        job.resume()
    else:
        job.run()
    return job


class TestTiledVsDirect:
    """Pixel-exact comparison of tiled output against the direct reference."""

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("kind", ["impulse", "impulse_offcenter",
                                      "edge_values", "random", "ramp"])
    def test_fixture_matrix_odd_kernel(self, store, settings, mode, kind):
        img = synthesize(kind, (61, 47), seed=9)
        kernel = KernelSpec.dense(make_kernel_weights("asymmetric", 3).reshape(3, 1)
                                  @ make_kernel_weights("box", 3).reshape(1, 3))
        job = run_tiled(store, settings, img, kernel, mode, tile_shape=(16, 16),
                        cval=0.125)
        expected = direct_reference(img, kernel, mode, cval=0.125)
        report = compare_arrays(job.open_output()[:], expected, atol=ATOL, rtol=0.0)
        assert report.passed, report.basis + f" max_abs_diff={report.max_abs_diff}"

    @pytest.mark.parametrize("mode", MODES)
    def test_irregular_tiles_even_kernel(self, store, settings, mode):
        # 103x89 image, 37x29 tiles -> ragged edges in both axes;
        # 6x4 kernel -> even in both axes, anchor (2, 1).
        img = synthesize("random", (103, 89), seed=21)
        weights = np.arange(1, 25, dtype=np.float64).reshape(6, 4) / 300.0
        kernel = KernelSpec.dense(weights)
        job = run_tiled(store, settings, img, kernel, mode, tile_shape=(37, 29))
        expected = direct_reference(img, kernel, mode)
        report = compare_arrays(job.open_output()[:], expected, atol=ATOL, rtol=0.0)
        assert report.passed, report.basis

    @pytest.mark.parametrize("mode", MODES)
    def test_large_kernel(self, store, settings, mode):
        # 63x63 kernel on a 90x80 image: halo (31) approaches half the image.
        img = synthesize("random", (90, 80), seed=33)
        g = make_kernel_weights("gaussian", 63)
        kernel = KernelSpec.dense(np.outer(g, g))
        job = run_tiled(store, settings, img, kernel, mode, tile_shape=(32, 32))
        expected = direct_reference(img, kernel, mode)
        report = compare_arrays(job.open_output()[:], expected, atol=1e-8, rtol=0.0)
        assert report.passed, report.basis

    @pytest.mark.parametrize("mode", MODES)
    def test_large_even_kernel(self, store, settings, mode):
        # 64x64 even kernel, default anchor (31, 31): asymmetric halo 31/32.
        img = synthesize("random", (70, 60), seed=41)
        g = make_kernel_weights("gaussian", 64)
        kernel = KernelSpec.dense(np.outer(g, g))
        job = run_tiled(store, settings, img, kernel, mode, tile_shape=(24, 24))
        expected = direct_reference(img, kernel, mode)
        report = compare_arrays(job.open_output()[:], expected, atol=1e-8, rtol=0.0)
        assert report.passed, report.basis

    @pytest.mark.parametrize("mode", MODES)
    def test_kernel_larger_than_tile(self, store, settings, mode):
        # kernel support (31) exceeds the tile (8x8): halo dominates the tile.
        img = synthesize("random", (40, 36), seed=55)
        g = make_kernel_weights("gaussian", 31)
        kernel = KernelSpec.dense(np.outer(g, g))
        job = run_tiled(store, settings, img, kernel, mode, tile_shape=(8, 8))
        expected = direct_reference(img, kernel, mode)
        report = compare_arrays(job.open_output()[:], expected, atol=1e-9, rtol=0.0)
        assert report.passed, report.basis

    @pytest.mark.parametrize("mode", MODES)
    def test_separable_large_kernel(self, store, settings, mode):
        img = synthesize("random", (96, 84), seed=61)
        kernel = KernelSpec.separable(make_kernel_weights("gaussian", 51),
                                      make_kernel_weights("box", 34))
        job = run_tiled(store, settings, img, kernel, mode, tile_shape=(20, 20))
        expected = direct_reference(img, kernel, mode)
        report = compare_arrays(job.open_output()[:], expected, atol=1e-9, rtol=0.0)
        assert report.passed, report.basis

    def test_separable_matches_dense_tiled(self, store, settings):
        img = synthesize("random", (50, 44), seed=71)
        col = make_kernel_weights("gaussian", 7)
        row = make_kernel_weights("asymmetric", 4)
        ks = KernelSpec.separable(col, row)
        kd = KernelSpec.dense(np.outer(col, row))
        out_s = run_tiled(store, settings, img, ks, BoundaryMode.MIRROR,
                          tile_shape=(13, 11)).open_output()[:]
        out_d = run_tiled(store, settings, img, kd, BoundaryMode.MIRROR,
                          tile_shape=(13, 11)).open_output()[:]
        np.testing.assert_allclose(out_s, out_d, atol=1e-10)

    @pytest.mark.parametrize("mode", MODES)
    def test_interrupt_resume_pixel_identical(self, store, settings, mode):
        img = synthesize("random", (45, 39), seed=81)
        kernel = KernelSpec.dense(make_kernel_weights("asymmetric", 5).reshape(5, 1)
                                  * np.ones((1, 3)))
        interrupted = run_tiled(store, settings, img, kernel, mode,
                                tile_shape=(16, 16), interrupt_at=3)
        straight = run_tiled(store, settings, img, kernel, mode,
                             tile_shape=(16, 16))
        np.testing.assert_array_equal(interrupted.open_output()[:],
                                      straight.open_output()[:])


class TestValidationReport:
    def test_report_carries_basis_versions_and_digests(self, store, settings):
        img = synthesize("impulse", (33, 27))
        kernel = KernelSpec.dense(np.ones((3, 3)) / 9.0)
        job = run_tiled(store, settings, img, kernel, BoundaryMode.PERIODIC,
                        tile_shape=(16, 16))
        report = validate_output(
            job.open_output()[:],
            store.open_array(job.state["spec"]["image_id"]),
            kernel, BoundaryMode.PERIODIC,
            context={"job_id": job.job_id},
        )
        assert report.passed
        assert "atol" in report.basis and "mismatches=0" in report.basis
        assert "scipy" in report.reference
        assert "numpy" in report.versions
        assert report.context["job_id"] == job.job_id

    def test_report_fails_on_real_deviation(self):
        actual = np.zeros((4, 4))
        expected = np.zeros((4, 4))
        expected[1, 1] = 1e-3
        report = compare_arrays(actual, expected, atol=1e-9, rtol=0.0)
        assert not report.passed
        assert report.mismatch_count == 1
        assert report.max_abs_diff == pytest.approx(1e-3)

    def test_report_flags_shape_mismatch(self):
        report = compare_arrays(np.zeros((4, 4)), np.zeros((4, 5)))
        assert not report.passed
        assert "shape mismatch" in report.basis
