"""Service-level decision tests: every rejection carries its category."""
from __future__ import annotations

import numpy as np
import pytest

from colorconvert.contract import ColorMode, ImageData
from colorconvert.errors import ConversionError, FailureCategory
from colorconvert.jobs import JobStatus
from colorconvert.kernel import RenderingIntent
from colorconvert.service import ConversionRequest


def _req(**kw):
    defaults = dict(
        image=ImageData(
            color=np.zeros((4, 4, 3), np.uint8), mode=ColorMode.RGB
        ),
        source_profile="srgb",
        target_profile="adobe-rgb",
    )
    defaults.update(kw)
    return ConversionRequest(**defaults)


def test_happy_path_completes(service, log):
    outcome = service.convert(_req(), log)
    assert outcome.job.status is JobStatus.COMPLETED
    assert outcome.result is not None
    assert outcome.job.report["rendering_intent"] == "relative_colorimetric"
    assert outcome.job.decisions[0].outcome == "accepted"


def test_unknown_source_profile_rejected(service, log):
    outcome = service.convert(_req(source_profile="nope"), log)
    assert outcome.job.status is JobStatus.REJECTED
    d = outcome.job.decisions[-1]
    assert d.category == FailureCategory.PROFILE_MISSING.value


def test_unknown_target_profile_rejected(service, log):
    outcome = service.convert(_req(target_profile="nope"), log)
    assert outcome.job.status is JobStatus.REJECTED
    assert outcome.job.decisions[-1].category == "profile_missing"


def test_embedded_source_without_profile_is_undecidable(service, log):
    outcome = service.convert(_req(source_profile="embedded"), log)
    assert outcome.job.status is JobStatus.UNDECIDABLE
    d = outcome.job.decisions[-1]
    assert d.category == FailureCategory.EMBEDDED_PROFILE_ABSENT.value
    assert "guess" in d.reason  # we refuse to assume sRGB


def test_embedded_source_with_valid_profile(service, log, srgb_icc_bytes):
    outcome = service.convert(
        _req(source_profile="embedded", embedded_profile=srgb_icc_bytes), log
    )
    assert outcome.job.status is JobStatus.COMPLETED
    assert outcome.job.report["source_profile_id"].startswith("embedded:")


def test_cmyk_requires_explicit_opt_in(service, log):
    outcome = service.convert(_req(target_profile="fogra39-cmyk"), log)
    assert outcome.job.status is JobStatus.REJECTED
    assert outcome.job.decisions[-1].category == "cmyk_restricted"


def test_cmyk_allowed_with_opt_in(service, log):
    outcome = service.convert(
        _req(target_profile="fogra39-cmyk", allow_cmyk=True), log
    )
    assert outcome.job.status is JobStatus.COMPLETED
    assert outcome.result.mode is ColorMode.CMYK
    assert outcome.result.color.shape[2] == 4


def test_rgb_profile_on_cmyk_image_rejected(service, log):
    img = ImageData(color=np.zeros((4, 4, 4), np.uint8), mode=ColorMode.CMYK)
    outcome = service.convert(
        _req(image=img, target_profile="srgb", allow_cmyk=True), log
    )
    assert outcome.job.status is JobStatus.REJECTED
    assert (
        outcome.job.decisions[-1].category
        == FailureCategory.PROFILE_COLORSPACE_MISMATCH.value
    )


def test_contract_violation_rejected(service, log):
    img = ImageData(color=np.zeros((4, 4, 3), np.float32), mode=ColorMode.RGB)
    outcome = service.convert(_req(image=img), log)
    assert outcome.job.status is JobStatus.REJECTED
    assert outcome.job.decisions[-1].category == "contract_violation"


def test_tile_size_out_of_bounds_rejected(service, log):
    outcome = service.convert(_req(tile_size=3), log)
    assert outcome.job.status is JobStatus.REJECTED
    assert outcome.job.decisions[-1].category == "contract_violation"


def test_pixel_limit_rejected(service, log, settings):
    big = ImageData(
        color=np.zeros((1, settings.max_pixels + 1, 3), np.uint8),
        mode=ColorMode.RGB,
    )
    outcome = service.convert(_req(image=big), log)
    assert outcome.job.status is JobStatus.REJECTED
    assert outcome.job.decisions[-1].category == "limit_exceeded"


def test_bad_intent_string_rejected():
    with pytest.raises(ConversionError) as ei:
        RenderingIntent.parse("vivid-colors-please")
    assert ei.value.category is FailureCategory.UNSUPPORTED_INTENT


def test_validate_endpoint_logic(service, log):
    ok = service.validate(_req(), log)
    assert ok.status is JobStatus.ACCEPTED
    bad = service.validate(_req(target_profile="nope"), log)
    assert bad.status is JobStatus.REJECTED
