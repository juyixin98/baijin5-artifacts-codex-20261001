"""Generate golden conversion values with the ICC engine directly.

Golden values are produced by driving littleCMS (via Pillow ImageCms)
*directly* - not through the iccconv kernel - so the kernel tests compare
against an independently produced reference.  Re-run after an engine
upgrade and review the diff.

Run:  python scripts/generate_golden.py
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO_ROOT / "profiles"
OUT = REPO_ROOT / "tests" / "golden" / "golden_values.json"

INTENTS = {"perceptual": 0, "relative_colorimetric": 1, "saturation": 2, "absolute_colorimetric": 3}

RGB_PATCHES = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow": (255, 255, 0),
    "mid_gray": (128, 128, 128),
    "quarter_gray": (64, 64, 64),
    "skin": (222, 184, 160),
    "sky": (90, 150, 220),
    "leaf": (60, 140, 70),
    "orange": (240, 130, 40),
    "dark_neutral": (20, 20, 20),
    "pastel": (200, 180, 220),
}
GRAY_PATCHES = {"g0": 0, "g32": 32, "g64": 64, "g128": 128, "g192": 192, "g255": 255}
CMYK_PATCHES = {
    "paper": (0, 0, 0, 0),
    "solid_black": (0, 0, 0, 255),
    "rich_black": (128, 128, 128, 255),
    "cyan": (255, 0, 0, 0),
    "magenta": (0, 255, 0, 0),
    "yellow": (0, 0, 255, 0),
    "mid": (96, 80, 72, 64),
}

# (case_id, src_profile, dst_profile, intent, bpc, patch_set, pil_mode)
CASES = [
    ("srgb_to_adobergb_relcol", "sRGB.icc", "AdobeRGB1998.icc", "relative_colorimetric", False, "RGB", "RGB"),
    ("srgb_to_adobergb_perceptual", "sRGB.icc", "AdobeRGB1998.icc", "perceptual", False, "RGB", "RGB"),
    ("srgb_to_adobergb_absolute", "sRGB.icc", "AdobeRGB1998.icc", "absolute_colorimetric", False, "RGB", "RGB"),
    ("adobergb_to_srgb_relcol", "AdobeRGB1998.icc", "sRGB.icc", "relative_colorimetric", False, "RGB", "RGB"),
    ("srgb_to_srgb_identity", "sRGB.icc", "sRGB.icc", "relative_colorimetric", False, "RGB", "RGB"),
    ("srgb_to_swapped_rg_relcol", "sRGB.icc", "SwappedRedAndGreen.icc", "relative_colorimetric", False, "RGB", "RGB"),
    ("srgb_to_swop_relcol_bpc", "sRGB.icc", "SWOP_TR003_coated_3.icc", "relative_colorimetric", True, "RGB", "CMYK"),
    ("srgb_to_swop_relcol", "sRGB.icc", "SWOP_TR003_coated_3.icc", "relative_colorimetric", False, "RGB", "CMYK"),
    ("srgb_to_swop_perceptual", "sRGB.icc", "SWOP_TR003_coated_3.icc", "perceptual", False, "RGB", "CMYK"),
    ("swop_to_srgb_relcol", "SWOP_TR003_coated_3.icc", "sRGB.icc", "relative_colorimetric", False, "CMYK", "RGB"),
    ("srgb_to_sgray_relcol", "sRGB.icc", "sgray.icc", "relative_colorimetric", False, "RGB", "L"),
    ("sgray_to_srgb_relcol", "sgray.icc", "sRGB.icc", "relative_colorimetric", False, "GRAY", "RGB"),
]

PATCH_SETS = {"RGB": RGB_PATCHES, "GRAY": GRAY_PATCHES, "CMYK": CMYK_PATCHES}


def convert_row(values: list[tuple[int, ...]], src: str, dst: str, intent: int, bpc: bool, out_mode: str) -> list[list[int]]:
    src_prof = ImageCms.getOpenProfile(str(PROFILES_DIR / src))
    dst_prof = ImageCms.getOpenProfile(str(PROFILES_DIR / dst))
    flags = ImageCms.FLAGS["BLACKPOINTCOMPENSATION"] if bpc else 0
    channels = len(values[0])
    in_mode = {1: "L", 3: "RGB", 4: "CMYK"}[channels]
    transform = ImageCms.buildTransformFromOpenProfiles(
        src_prof, dst_prof, in_mode, out_mode, renderingIntent=intent, flags=flags,
    )
    arr = np.array(values, dtype=np.uint8)
    arr = arr.reshape(1, -1) if channels == 1 else arr.reshape(1, -1, channels)
    img = Image.fromarray(arr, mode=in_mode)
    out = ImageCms.applyTransform(img, transform)
    return np.asarray(out, dtype=np.uint8).reshape(len(values), -1).tolist()


def main() -> None:
    cases = []
    for case_id, src, dst, intent_name, bpc, patch_set, _mode in CASES:
        patches = PATCH_SETS[patch_set]
        names = list(patches)
        values = [patches[n] if isinstance(patches[n], tuple) else (patches[n],) for n in names]
        expected = convert_row(values, src, dst, INTENTS[intent_name], bpc, _mode)
        cases.append(
            {
                "id": case_id,
                "src_profile": src,
                "dst_profile": dst,
                "intent": intent_name,
                "black_point_compensation": bpc,
                "patch_names": names,
                "input": [list(v) for v in values],
                "expected": expected,
            }
        )
    payload = {
        "generator": "scripts/generate_golden.py (littleCMS via Pillow ImageCms, direct)",
        "engine": {"lcms": ImageCms.versions()[1], "pillow": ImageCms.versions()[3]},
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": cases,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    print(f"wrote {OUT} with {len(cases)} cases, engine {payload['engine']}")


if __name__ == "__main__":
    main()
