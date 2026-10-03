"""End-to-end API tests via FastAPI TestClient."""
from __future__ import annotations

import base64
import io
import logging

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from colorconvert.api import create_app
from tests.conftest import make_png_b64, rgb_image


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def _payload(image_b64, **kw):
    body = {
        "image_b64": image_b64,
        "source_profile": "srgb",
        "target_profile": "adobe-rgb",
    }
    body.update(kw)
    return body


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_list_profiles(client):
    profiles = client.get("/v1/profiles").json()["profiles"]
    by_id = {p["id"]: p for p in profiles}
    assert by_id["srgb"]["color_space"] == "RGB"
    assert by_id["fogra39-cmyk"]["cmyk_restricted"] is True
    assert len(by_id["srgb"]["sha256_12"]) == 12


def test_convert_rgb_png(client, png_rgb_b64):
    r = client.post("/v1/convert", json=_payload(png_rgb_b64))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    report = body["report"]
    assert report["rendering_intent"] == "relative_colorimetric"
    assert report["black_point_compensation"] is False
    assert report["engine"].startswith("LittleCMS")
    out = Image.open(io.BytesIO(base64.b64decode(body["image_b64"])))
    assert out.mode == "RGB"
    assert out.size == (8, 8)
    # job is retrievable and carries the same request id
    job = client.get(f"/v1/jobs/{body['job_id']}").json()
    assert job["request_id"] == body["request_id"]


def test_convert_records_intent_and_bpc(client, png_rgb_b64):
    r = client.post(
        "/v1/convert",
        json=_payload(
            png_rgb_b64,
            target_profile="fogra39-cmyk",
            allow_cmyk=True,
            rendering_intent="perceptual",
            black_point_compensation=True,
        ),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report"]["rendering_intent"] == "perceptual"
    assert body["report"]["black_point_compensation"] is True
    assert body["image_format"] == "tiff"
    out = Image.open(io.BytesIO(base64.b64decode(body["image_b64"])))
    assert out.mode == "CMYK"


def test_convert_rgba_preserves_alpha(client):
    arr = np.dstack(
        [rgb_image(), np.arange(64, dtype=np.uint8).reshape(8, 8) * 3]
    )
    r = client.post("/v1/convert", json=_payload(make_png_b64(arr, "RGBA")))
    assert r.status_code == 200, r.text
    out = Image.open(io.BytesIO(base64.b64decode(r.json()["image_b64"])))
    assert out.mode == "RGBA"
    alpha = np.asarray(out)[..., 3]
    assert np.array_equal(alpha, arr[..., 3])


def test_embedded_source_without_profile_undecidable(client, png_rgb_b64):
    r = client.post(
        "/v1/convert", json=_payload(png_rgb_b64, source_profile="embedded")
    )
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "undecidable"
    assert body["decisions"][-1]["category"] == "embedded_profile_absent"


def test_embedded_source_with_profile(client, srgb_icc_bytes):
    b64 = make_png_b64(rgb_image(), "RGB", icc=srgb_icc_bytes)
    r = client.post("/v1/convert", json=_payload(b64, source_profile="embedded"))
    assert r.status_code == 200, r.text
    assert r.json()["report"]["source_profile_id"].startswith("embedded:")


def test_unknown_target_rejected(client, png_rgb_b64):
    r = client.post(
        "/v1/convert", json=_payload(png_rgb_b64, target_profile="nope")
    )
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "rejected"
    assert body["decisions"][-1]["category"] == "profile_missing"


def test_cmyk_requires_opt_in(client, png_rgb_b64):
    r = client.post(
        "/v1/convert", json=_payload(png_rgb_b64, target_profile="fogra39-cmyk")
    )
    assert r.status_code == 422
    assert r.json()["decisions"][-1]["category"] == "cmyk_restricted"


def test_bad_base64_rejected(client):
    r = client.post("/v1/convert", json=_payload("!!!not-base64!!!"))
    assert r.status_code == 422
    assert r.json()["decisions"][-1]["category"] == "image_decode_failed"


def test_bad_intent_rejected(client, png_rgb_b64):
    r = client.post(
        "/v1/convert", json=_payload(png_rgb_b64, rendering_intent="magic")
    )
    assert r.status_code == 422
    assert r.json()["decisions"][-1]["category"] == "unsupported_intent"


def test_validate_endpoint(client, png_rgb_b64):
    ok = client.post("/v1/validate", json=_payload(png_rgb_b64))
    assert ok.status_code == 200
    assert ok.json()["status"] == "accepted"
    bad = client.post(
        "/v1/validate", json=_payload(png_rgb_b64, target_profile="nope")
    )
    assert bad.status_code == 422
    assert bad.json()["status"] == "rejected"


def test_job_not_found(client):
    assert client.get("/v1/jobs/nonexistent").status_code == 404


def test_logs_carry_request_id_and_are_redacted(client, png_rgb_b64):
    import io as _io

    stream = _io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        logging.Formatter("[request_id=%(request_id)s] %(message)s")
    )
    logger = logging.getLogger("colorconvert")
    logger.addHandler(handler)
    try:
        r = client.post("/v1/convert", json=_payload(png_rgb_b64))
    finally:
        logger.removeHandler(handler)
    assert r.status_code == 200
    text = stream.getvalue()
    assert r.json()["request_id"] in text
    assert "decision=accepted" in text
    # the base64 image payload must never appear in the logs
    assert png_rgb_b64[:64] not in text
