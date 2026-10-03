"""End-to-end validation suite: concrete numeric assertions per fixture,
plus the independent reference cross-check."""

import json

from app.validation import run_validation


def test_validation_suite_all_pass(tmp_path, cfg):
    from app.fixtures import generate_all

    generate_all(tmp_path)
    report = run_validation(fixtures_dir=tmp_path, cfg=cfg)

    summary = report["summary"]
    assert summary["total"] == 7
    assert summary["all_passed"], json.dumps(
        [e for e in report["fixtures"] if not e["passed"]], indent=2
    )

    entries = {e["name"]: e for e in report["fixtures"]}

    # Concrete numeric expectations, not just "endpoint ran".
    assert entries["integer_shift"]["localization_error_px"] < 0.15
    assert entries["subpixel_shift"]["localization_error_px"] < 0.2
    assert entries["brightness_change"]["localization_error_px"] < 0.3
    assert entries["brightness_change"]["status"] == "ok"

    periodic = entries["periodic_texture"]
    assert periodic["status"] == "uncertain"
    assert "AMBIGUOUS_PEAKS" in periodic["uncertainties"]
    assert len(periodic["ambiguity_peaks"]) >= 2

    assert entries["constant_image"]["status"] == "failed"
    assert "DEGENERATE_SPECTRUM" in entries["constant_image"]["failures"]

    assert entries["low_overlap"]["status"] != "ok"
    assert entries["independent_pair"]["status"] != "ok"

    # Independent reference cross-checked the kernel on textured fixtures.
    for name in ("integer_shift", "subpixel_shift", "brightness_change", "low_overlap"):
        assert entries[name]["reference"] is not None, name

    # Environment + config are recorded for reproducibility.
    assert report["environment"]["numpy"]
    assert report["config"]["window"] == "hann"
