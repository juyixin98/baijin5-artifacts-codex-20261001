"""Job tests: interrupt/resume, digest binding, reports, structured logs."""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.contract import BoundaryMode
from app.jobs import (
    DigestMismatchError,
    JobSpec,
    JobStateError,
    TiledJobRunner,
    UnknownJobError,
    build_image,
    build_kernel,
    load_runner,
)
from app.kernels import filter_direct


def make_runner(settings, spec_dict):
    return TiledJobRunner(spec=JobSpec.from_dict(spec_dict), settings=settings)


class TestLifecycle:
    def test_run_to_completion(self, settings, noise_spec_dict):
        runner = make_runner(settings, noise_spec_dict)
        manifest = runner.create()
        assert manifest["status"] == "pending"
        result = runner.execute()
        assert result["status"] == "completed"
        assert result["progress"]["done"] == result["progress"]["total"]

        # Output equals the single-pass direct computation.
        image = build_image(runner.spec.image, settings)
        kernel = build_kernel(runner.spec.kernel, settings)
        direct = filter_direct(image.data, kernel, BoundaryMode.MIRROR)
        np.testing.assert_allclose(runner.result_array(), direct, atol=1e-12)

    def test_double_create_rejected(self, settings, noise_spec_dict):
        runner = make_runner(settings, noise_spec_dict)
        runner.create()
        with pytest.raises(JobStateError):
            runner.create()

    def test_unknown_job(self, settings):
        with pytest.raises(UnknownJobError) as exc:
            load_runner("does-not-exist", settings)
        assert exc.value.category == "unknown_job"


class TestInterruptResume:
    def test_resume_completes_and_matches_uninterrupted(self, settings,
                                                        noise_spec_dict):
        # Interrupted then resumed run.
        runner = make_runner(settings, noise_spec_dict)
        manifest = runner.create()
        total = len(manifest["tiles"])
        assert total > 2
        first = runner.execute(max_tiles=2)
        assert first["status"] == "interrupted"
        assert first["progress"]["done"] == 2
        resumed = load_runner(first["job_id"], settings).execute()
        assert resumed["status"] == "completed"
        out_resumed = runner.result_array()

        # Uninterrupted run of the same spec in a fresh workspace.
        from app.config import Settings

        clean = Settings(workspace_dir=settings.workspace_dir / "clean",
                         log_dir=settings.log_dir)
        runner2 = TiledJobRunner(spec=runner.spec, settings=clean)
        runner2.create()
        runner2.execute()
        np.testing.assert_array_equal(out_resumed, runner2.result_array())

    def test_resume_rejects_changed_kernel(self, settings, noise_spec_dict):
        runner = make_runner(settings, noise_spec_dict)
        runner.create()
        runner.execute(max_tiles=1)

        tampered = dict(noise_spec_dict)
        tampered["kernel"] = {"kind": "gaussian", "k": 5, "sigma": 9.9}
        # Same job directory trick: rebuild a runner whose spec hashes to the
        # same job id is impossible after tampering, so instead tamper the
        # on-disk manifest's spec while keeping the original digests.
        job_dir = runner.job_dir
        manifest_path = job_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["spec"]["kernel"] = tampered["kernel"]
        manifest_path.write_text(json.dumps(manifest))

        resumed_runner = load_runner(manifest["job_id"], settings)
        with pytest.raises(DigestMismatchError) as exc:
            resumed_runner.execute()
        assert exc.value.category == "digest_mismatch"

    def test_resume_rejects_changed_input(self, settings, noise_spec_dict):
        runner = make_runner(settings, noise_spec_dict)
        runner.create()
        runner.execute(max_tiles=1)
        job_dir = runner.job_dir
        manifest_path = job_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["spec"]["image"] = {"kind": "noise", "shape": [70, 54],
                                     "seed": 999}
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(DigestMismatchError):
            load_runner(manifest["job_id"], settings).execute()


class TestReportAndLogs:
    def test_report_contents(self, settings, noise_spec_dict):
        runner = make_runner(settings, noise_spec_dict)
        runner.create()
        runner.execute()
        report = runner.report()
        assert report["status"] == "completed"
        assert report["peak_rss_bytes"] > 0
        assert len(report["output_digest"]) == 64
        assert {"python", "numpy", "scipy"} <= set(report["versions"])

    def test_run_log_records_steps_and_identity(self, settings,
                                                noise_spec_dict):
        runner = make_runner(settings, noise_spec_dict)
        manifest = runner.create()
        runner.execute(max_tiles=1)
        log_path = settings.log_dir / f"run-{manifest['job_id']}.log"
        assert log_path.exists()
        records = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert records[0]["event"] == "execute_start"
        assert records[0]["input_digest"] == manifest["input_digest"]
        assert records[0]["kernel_digest"] == manifest["kernel_digest"]
        tile_records = [r for r in records if r["event"] == "tile_done"]
        assert len(tile_records) == 1
        assert "progress" in tile_records[0]
        assert all("versions" in r for r in records)
        assert all(r["run_id"] == manifest["job_id"] for r in records)

    def test_result_before_completion_rejected(self, settings, noise_spec_dict):
        runner = make_runner(settings, noise_spec_dict)
        runner.create()
        runner.execute(max_tiles=1)
        with pytest.raises(JobStateError):
            runner.result_array()

    def test_deterministic_output_digest(self, settings, noise_spec_dict):
        from app.config import Settings

        digests = []
        for i in range(2):
            ws = Settings(workspace_dir=settings.workspace_dir / f"r{i}",
                          log_dir=settings.log_dir)
            runner = TiledJobRunner(spec=JobSpec.from_dict(noise_spec_dict),
                                    settings=ws)
            runner.create()
            runner.execute()
            digests.append(runner.report()["output_digest"])
        assert digests[0] == digests[1]
