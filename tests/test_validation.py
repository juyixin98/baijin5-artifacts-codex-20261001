"""Validation policy tests: reject vs clip, and each failure category."""

from __future__ import annotations

import numpy as np
import pytest

from app.config import Settings
from app.imaging import ImageContractError, decode_png, encode_png
from app.schemas import (
    Connectivity,
    Diagnostics,
    Engine,
    FailureCategory,
    JobStatus,
    ViolationPolicy,
)
from app.service import ReconstructionRejected, reconstruct, validate_only

SETTINGS = Settings(max_image_side=64)


def _pair():
    mask = np.full((8, 8), 200, dtype=np.uint8)
    marker = np.zeros_like(mask)
    marker[3, 3] = 100
    return marker, mask


def test_valid_pair_accepted():
    marker, mask = _pair()
    result, diag = reconstruct(
        marker,
        mask,
        connectivity=Connectivity.EIGHT,
        engine=Engine.QUEUE,
        on_violation=ViolationPolicy.REJECT,
        settings=SETTINGS,
    )
    assert diag.status is JobStatus.ACCEPTED
    assert diag.request_id
    assert np.all(result == 100)


def test_marker_above_mask_rejected_with_category():
    marker, mask = _pair()
    marker[0, 0] = 250  # exceeds mask value 200
    with pytest.raises(ReconstructionRejected) as excinfo:
        reconstruct(
            marker,
            mask,
            connectivity=Connectivity.EIGHT,
            engine=Engine.QUEUE,
            on_violation=ViolationPolicy.REJECT,
            settings=SETTINGS,
        )
    diag = excinfo.value.diagnostics
    assert diag.status is JobStatus.REJECTED
    assert diag.failure_category is FailureCategory.MARKER_EXCEEDS_MASK
    assert diag.violation_pixels == 1


def test_marker_above_mask_clipped_matches_explicit_min():
    marker, mask = _pair()
    marker[0, 0] = 250
    result, diag = reconstruct(
        marker,
        mask,
        connectivity=Connectivity.EIGHT,
        engine=Engine.QUEUE,
        on_violation=ViolationPolicy.CLIP,
        settings=SETTINGS,
    )
    assert diag.status is JobStatus.CLIPPED
    assert diag.violation_pixels == 1
    # Result must equal reconstruction of the explicitly clipped marker.
    expected, _ = reconstruct(
        np.minimum(marker, mask),
        mask,
        connectivity=Connectivity.EIGHT,
        engine=Engine.QUEUE,
        on_violation=ViolationPolicy.REJECT,
        settings=SETTINGS,
    )
    np.testing.assert_array_equal(result, expected)


def test_shape_mismatch_rejected():
    marker, mask = _pair()
    bad_mask = mask[:-1]
    with pytest.raises(ReconstructionRejected) as excinfo:
        reconstruct(
            marker,
            bad_mask,
            connectivity=Connectivity.EIGHT,
            engine=Engine.QUEUE,
            on_violation=ViolationPolicy.REJECT,
            settings=SETTINGS,
        )
    assert (
        excinfo.value.diagnostics.failure_category
        is FailureCategory.SHAPE_MISMATCH
    )


def test_oversized_image_rejected():
    marker = np.zeros((65, 4), dtype=np.uint8)
    mask = np.full((65, 4), 255, dtype=np.uint8)
    diag = validate_only(
        marker,
        mask,
        connectivity=Connectivity.EIGHT,
        on_violation=ViolationPolicy.REJECT,
        settings=SETTINGS,
    )
    assert diag.status is JobStatus.REJECTED
    assert diag.failure_category is FailureCategory.IMAGE_TOO_LARGE


def test_empty_marker_rejected():
    mask = np.full((8, 8), 200, dtype=np.uint8)
    marker = np.zeros_like(mask)
    diag = validate_only(
        marker,
        mask,
        connectivity=Connectivity.EIGHT,
        on_violation=ViolationPolicy.REJECT,
        settings=SETTINGS,
    )
    assert diag.status is JobStatus.REJECTED
    assert diag.failure_category is FailureCategory.EMPTY_MARKER


def test_diagnostics_are_desensitized():
    marker, mask = _pair()
    _, diag = reconstruct(
        marker,
        mask,
        connectivity=Connectivity.EIGHT,
        engine=Engine.QUEUE,
        on_violation=ViolationPolicy.REJECT,
        settings=SETTINGS,
    )
    # Hashes identify the content for correlation without exposing it.
    assert diag.marker_sha256_12 and len(diag.marker_sha256_12) == 12
    assert diag.mask_sha256_12 and len(diag.mask_sha256_12) == 12
    dumped = diag.model_dump_json()
    # Only contract-level metadata may appear — no pixel payloads.
    import json

    allowed = set(Diagnostics.model_fields)
    assert set(json.loads(dumped)) <= allowed


def test_decode_rejects_rgb():
    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    with pytest.raises(ImageContractError) as excinfo:
        decode_png(buf.getvalue(), label="marker")
    assert excinfo.value.category is FailureCategory.NOT_GRAYSCALE


def test_decode_rejects_garbage():
    with pytest.raises(ImageContractError) as excinfo:
        decode_png(b"this is not a png", label="mask")
    assert excinfo.value.category is FailureCategory.DECODE_ERROR


def test_png_roundtrip():
    marker, _ = _pair()
    decoded = decode_png(encode_png(marker), label="marker")
    np.testing.assert_array_equal(decoded, marker)
