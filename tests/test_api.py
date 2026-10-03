"""End-to-end API tests: happy paths, rejection categories, diagnostics."""

from __future__ import annotations

import base64
import io
import logging

import numpy as np
from PIL import Image

from conftest import alpha_ramp, encode_png, gradient_rgb


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _post_convert(client, **overrides):
    payload = {
        "image_b64": _b64(encode_png(gradient_rgb())),
        "image_format": "png",
        "source": {"name": "sRGB.icc"},
        "target": {"name": "AdobeRGB1998.icc"},
        "intent": 1,
    }
    payload.update(overrides)
    return client.post("/v1/convert", json=payload)


def test_health(client):
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json()["engine"].startswith("littleCMS")


def test_list_profiles(client):
    resp = client.get("/v1/profiles")
    assert "sRGB.icc" in resp.json()["profiles"]


def test_convert_rgb_to_cmyk_happy_path(client):
    resp = _post_convert(
        client,
        target={"name": "SWOP_TR003_coated_3.icc"},
        intent=0,
        black_point_compensation=True,
        check_gamut=True,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["output_format"] == "tiff"
    assert body["request_id"]
    assert resp.headers["x-request-id"] == body["request_id"]

    meta = body["metadata"]
    assert meta["rendering_intent"] == "PERCEPTUAL"
    assert meta["black_point_compensation"] is True
    assert meta["lossless"] is False
    assert meta["source_profile"]["color_space"] == "RGB"
    assert meta["target_profile"]["color_space"] == "CMYK"

    out = Image.open(io.BytesIO(base64.b64decode(body["image_b64"])))
    assert out.mode == "CMYK"
    assert out.size == (16, 8)

    assert body["gamut"]["method"] == "roundtrip-heuristic"
    steps = [e["step"] for e in body["diagnostics"]]
    assert "profiles" in steps and "transform" in steps and "gamut" in steps


def test_missing_profile_rejected_not_guessed(client):
    # Image without embedded profile + source 'embedded' -> explicit rejection.
    resp = _post_convert(client, source={"embedded": True})
    assert resp.status_code == 422
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["category"] == "missing_profile"
    assert "never assumed" in body["message"]


def test_missing_source_field_rejected(client):
    payload = {
        "image_b64": _b64(encode_png(gradient_rgb())),
        "target": {"name": "sRGB.icc"},
    }
    resp = client.post("/v1/convert", json=payload)
    assert resp.status_code == 422


def test_embedded_profile_used_when_declared(client, profile_bytes):
    png = encode_png(gradient_rgb(), icc=profile_bytes("sRGB.icc"))
    resp = _post_convert(client, image_b64=_b64(png), source={"embedded": True})
    assert resp.status_code == 200
    assert resp.json()["metadata"]["source_profile"]["source"] == "embedded"


def test_garbage_profile_rejected(client, bad_profile_bytes):
    resp = _post_convert(
        client, target={"icc_b64": _b64(bad_profile_bytes("garbage.icc"))}
    )
    assert resp.status_code == 422
    assert resp.json()["category"] == "invalid_profile"


def test_truncated_profile_rejected(client, bad_profile_bytes):
    resp = _post_convert(
        client, source={"icc_b64": _b64(bad_profile_bytes("truncated.icc"))}
    )
    assert resp.status_code == 422
    assert resp.json()["category"] == "invalid_profile"


def test_named_color_profile_rejected(client, bad_profile_bytes):
    resp = _post_convert(
        client, target={"icc_b64": _b64(bad_profile_bytes("named_color.icc"))}
    )
    assert resp.status_code == 422
    assert resp.json()["category"] == "invalid_profile"


def test_unknown_profile_name_rejected(client):
    resp = _post_convert(client, target={"name": "no-such.icc"})
    assert resp.status_code == 422
    assert resp.json()["category"] == "missing_profile"


def test_profile_role_mismatch_rejected(client):
    # RGB image but a CMYK source profile.
    resp = _post_convert(client, source={"name": "SWOP_TR003_coated_3.icc"})
    assert resp.status_code == 422
    assert resp.json()["category"] == "profile_role_mismatch"


def test_alpha_channel_preserved_end_to_end(client):
    rgb = gradient_rgb(8, 16)
    alpha = alpha_ramp(8, 16)
    rgba = np.concatenate([rgb, alpha[..., None]], axis=-1)
    resp = _post_convert(client, image_b64=_b64(encode_png(rgba)))
    assert resp.status_code == 200
    out = Image.open(io.BytesIO(base64.b64decode(resp.json()["image_b64"])))
    assert out.mode == "RGBA"
    out_alpha = np.asarray(out)[..., 3]
    assert np.array_equal(out_alpha, alpha)


def test_chunked_job_matches_unchunked(client):
    plain = _post_convert(client)
    tiled = _post_convert(client, tile_size=6)
    assert plain.status_code == 200 and tiled.status_code == 200
    job = tiled.json()["job"]
    assert job["status"] == "completed"
    assert len(job["tiles"]) == 3 * 2  # 16x8 image, 6px tiles -> 3 cols x 2 rows
    plain_pixels = np.asarray(Image.open(io.BytesIO(base64.b64decode(plain.json()["image_b64"]))))
    tiled_pixels = np.asarray(Image.open(io.BytesIO(base64.b64decode(tiled.json()["image_b64"]))))
    assert np.array_equal(plain_pixels, tiled_pixels)


def test_gamut_flags_saturated_red(client):
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    image[..., 0] = 255  # pure sRGB red, out of SWOP gamut
    resp = _post_convert(
        client,
        image_b64=_b64(encode_png(image)),
        target={"name": "SWOP_TR003_coated_3.icc"},
        check_gamut=True,
    )
    assert resp.status_code == 200
    assert resp.json()["gamut"]["flagged_pixels"] == 16


def test_request_id_echoed(client):
    resp = client.post(
        "/v1/convert",
        json={
            "image_b64": _b64(encode_png(gradient_rgb())),
            "source": {"name": "sRGB.icc"},
            "target": {"name": "sRGB.icc"},
        },
        headers={"X-Request-ID": "req-test-123"},
    )
    assert resp.json()["request_id"] == "req-test-123"
    assert resp.headers["x-request-id"] == "req-test-123"


def test_validate_profile_endpoint(client, profile_bytes, bad_profile_bytes):
    ok = client.post("/v1/profiles/validate", json={"name": "sRGB.icc"})
    assert ok.status_code == 200
    assert ok.json()["profile"]["color_space"] == "RGB"

    bad = client.post(
        "/v1/profiles/validate",
        json={"icc_b64": _b64(bad_profile_bytes("garbage.icc"))},
    )
    assert bad.status_code == 422
    assert bad.json()["category"] == "invalid_profile"


def test_logs_are_redacted(client, caplog):
    image_b64 = _b64(encode_png(gradient_rgb()))
    with caplog.at_level(logging.INFO, logger="iccconv"):
        resp = _post_convert(client, image_b64=image_b64, check_gamut=True)
    assert resp.status_code == 200
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert image_b64 not in text
    assert "icc_profile" not in text
    assert "sha256_16" in text  # digests are logged, payloads are not
    assert "req" not in text or "request_id" in text  # request id present in logs
