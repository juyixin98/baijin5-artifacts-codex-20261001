"""Minimal Python client example for the translation-estimation service.

Usage:  python -m uvicorn app.main:app --port 8000   (in another shell)
        python examples/estimate_request.py
"""
import base64
import json
import os
import urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8000")


def post(path: str, payload: dict, request_id: str) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-request-id": request_id},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main() -> None:
    # 1. fixture-based estimate (known ground truth: dy=2.4, dx=-1.7)
    out = post("/v1/estimate", {
        "reference": {"fixture_id": "subpixel_shift", "role": "reference"},
        "moving": {"fixture_id": "subpixel_shift", "role": "moving"},
    }, request_id="py-example-1")
    print("status:", out["status"], "shift:", out["shift"])
    print("confidence:", {k: round(v, 4) for k, v in out["confidence"].items()
                          if isinstance(v, float)})

    # 2. upload-based estimate
    with open("fixtures/data/integer_shift_ref.png", "rb") as f:
        ref_b64 = base64.b64encode(f.read()).decode()
    with open("fixtures/data/integer_shift_mov.png", "rb") as f:
        mov_b64 = base64.b64encode(f.read()).decode()
    out = post("/v1/estimate", {
        "reference": {"png_base64": ref_b64},
        "moving": {"png_base64": mov_b64},
    }, request_id="py-example-2")
    print("uploaded pair -> status:", out["status"], "shift:", out["shift"])

    # 3. validation report over all fixtures
    out = post("/v1/validate", {"tolerance_px": 0.5}, request_id="py-example-3")
    print("validation:", out["n_category_match"], "/", out["n_fixtures"],
          "categories matched")
    for rep in out["reports"]:
        print(" ", rep["fixture_id"], "->", rep["observed_status"],
              rep.get("failure_reason") or "", rep["uncertainties"],
              "err_px=", rep.get("localization_error_px"))


if __name__ == "__main__":
    main()
