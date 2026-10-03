"""Deterministic synthetic fixtures (verification materials).

Ground truth is known by construction: image B is a crop of the *same*
canvas as image A after a whole-canvas subpixel shift
(``scipy.ndimage.shift``, order-3 spline), so the true translation is
exactly the applied shift — it is not produced by the kernel under test.

Fixture categories cover the contract's verification list: known integer
and subpixel shifts, periodic texture (ambiguity), constant image
(degenerate spectrum), low overlap, brightness change, and a genuinely
non-overlapping independent pair.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter
from scipy.ndimage import shift as ndi_shift

CANVAS_SIZE = 256
WINDOW_SIZE = 128
WINDOW_ORIGIN = 64  # crop window inside the canvas


@dataclass
class Fixture:
    name: str
    category: str
    img_a: np.ndarray  # float64, 0..255
    img_b: np.ndarray
    ground_truth_shift: tuple[float, float] | None  # (dy, dx) of B rel. to A
    expected_status: str  # "ok" | "uncertain" | "failed" | "not_ok"
    expected_reasons: list[str]
    tolerance_px: float | None  # pass threshold for ok fixtures
    reference_max_shift: int | None  # bounded search range for the NCC reference
    note: str

    def manifest_entry(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "ground_truth_shift": self.ground_truth_shift,
            "expected_status": self.expected_status,
            "expected_reasons": self.expected_reasons,
            "tolerance_px": self.tolerance_px,
            "reference_max_shift": self.reference_max_shift,
            "note": self.note,
            "files": [f"{self.name}_a.png", f"{self.name}_b.png"],
        }


def _texture_canvas(seed: int) -> np.ndarray:
    """Aperiodic band-limited noise: full, well-conditioned spectrum."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=(CANVAS_SIZE, CANVAS_SIZE))
    smooth = gaussian_filter(noise, sigma=3.0)
    smooth -= smooth.min()
    return smooth / smooth.max() * 255.0


def _periodic_canvas(cell: int = 8) -> np.ndarray:
    """Checkerboard (period 2*cell): harmonic-rich lattice spectrum, so the
    whitened correlation surface keeps genuine replica peaks — the ambiguity
    the validation suite must surface. A little noise breaks exact ties."""
    yy, xx = np.mgrid[0:CANVAS_SIZE, 0:CANVAS_SIZE]
    board = ((yy // cell + xx // cell) % 2).astype(np.float64) * 200.0
    rng = np.random.default_rng(7)
    board += rng.normal(scale=3.0, size=board.shape)
    # Clip into the 8-bit range so the in-memory fixture and its saved PNG
    # quantise identically (round-trip stays within 0.5 gray levels).
    return np.clip(board, 0.0, 255.0)


def _crop(img: np.ndarray) -> np.ndarray:
    o, w = WINDOW_ORIGIN, WINDOW_SIZE
    return img[o : o + w, o : o + w].copy()


def _shifted_pair(
    canvas: np.ndarray, shift: tuple[float, float]
) -> tuple[np.ndarray, np.ndarray]:
    shifted = ndi_shift(canvas, shift, order=3, mode="nearest", prefilter=True)
    return _crop(canvas), _crop(shifted)


def build_fixtures() -> list[Fixture]:
    """Build all fixtures in memory (float64, no PNG quantisation)."""
    fixtures: list[Fixture] = []

    a, b = _shifted_pair(_texture_canvas(seed=11), (12.0, -7.0))
    fixtures.append(
        Fixture(
            name="integer_shift",
            category="integer_shift",
            img_a=a,
            img_b=b,
            ground_truth_shift=(12.0, -7.0),
            expected_status="ok",
            expected_reasons=[],
            tolerance_px=0.15,
            reference_max_shift=20,
            note="Known integer translation of aperiodic texture.",
        )
    )

    a, b = _shifted_pair(_texture_canvas(seed=12), (5.4, -3.65))
    fixtures.append(
        Fixture(
            name="subpixel_shift",
            category="subpixel_shift",
            img_a=a,
            img_b=b,
            ground_truth_shift=(5.4, -3.65),
            expected_status="ok",
            expected_reasons=[],
            tolerance_px=0.2,
            reference_max_shift=12,
            note="Known subpixel translation; exercises parabolic refinement.",
        )
    )

    a, b = _shifted_pair(_texture_canvas(seed=13), (8.25, 6.4))
    b_bright = np.clip(0.65 * b + 35.0, 0.0, 255.0)  # global gain + offset
    fixtures.append(
        Fixture(
            name="brightness_change",
            category="brightness_change",
            img_a=a,
            img_b=b_bright,
            ground_truth_shift=(8.25, 6.4),
            expected_status="ok",
            expected_reasons=[],
            tolerance_px=0.3,
            reference_max_shift=16,
            note="Gain 0.65 + offset 35 on B; normalisation must absorb it.",
        )
    )

    a, b = _shifted_pair(_periodic_canvas(cell=8), (4.0, 4.0))
    fixtures.append(
        Fixture(
            name="periodic_texture",
            category="periodic_texture",
            img_a=a,
            img_b=b,
            ground_truth_shift=(4.0, 4.0),
            expected_status="uncertain",
            expected_reasons=["AMBIGUOUS_PEAKS"],
            tolerance_px=None,
            reference_max_shift=None,
            note="Period-16 checkerboard: lattice replica peaks must be flagged.",
        )
    )

    constant = np.full((CANVAS_SIZE, CANVAS_SIZE), 128.0)
    a, b = _shifted_pair(constant, (3.0, 3.0))
    fixtures.append(
        Fixture(
            name="constant_image",
            category="constant_image",
            img_a=a,
            img_b=b,
            ground_truth_shift=(3.0, 3.0),
            expected_status="failed",
            expected_reasons=["DEGENERATE_SPECTRUM"],
            tolerance_px=None,
            reference_max_shift=None,
            note="Zero-variance pair: cross-power spectrum is all zeros.",
        )
    )

    a, b = _shifted_pair(_texture_canvas(seed=14), (52.0, 52.0))
    fixtures.append(
        Fixture(
            name="low_overlap",
            category="low_overlap",
            img_a=a,
            img_b=b,
            ground_truth_shift=(52.0, 52.0),
            expected_status="not_ok",
            expected_reasons=["INSUFFICIENT_OVERLAP"],
            tolerance_px=None,
            reference_max_shift=60,
            note="Shift 52/128 px per axis -> ~35% overlap, below the 50% bar.",
        )
    )

    a = _crop(_texture_canvas(seed=15))
    b = _crop(_texture_canvas(seed=16))
    fixtures.append(
        Fixture(
            name="independent_pair",
            category="independent_pair",
            img_a=a,
            img_b=b,
            ground_truth_shift=None,
            expected_status="not_ok",
            expected_reasons=[],  # any listed reason satisfies the contract
            tolerance_px=None,
            reference_max_shift=None,
            note="Two unrelated textures: true non-overlap, no valid shift.",
        )
    )

    return fixtures


def _save_png(arr: np.ndarray, path: Path) -> None:
    quantised = np.clip(np.rint(arr), 0, 255).astype(np.uint8)
    Image.fromarray(quantised, mode="L").save(path, format="PNG")


def generate_all(out_dir: Path) -> list[Fixture]:
    """Write PNGs + manifest.json; returns the in-memory fixtures."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures = build_fixtures()
    manifest = {
        "canvas_size": CANVAS_SIZE,
        "window_size": WINDOW_SIZE,
        "convention": "img_b[y, x] ~= img_a[y - dy, x - dx]",
        "fixtures": [fx.manifest_entry() for fx in fixtures],
    }
    for fx in fixtures:
        _save_png(fx.img_a, out_dir / f"{fx.name}_a.png")
        _save_png(fx.img_b, out_dir / f"{fx.name}_b.png")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return fixtures


def _load_png(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return np.asarray(img.convert("L"), dtype=np.float64)


def load_fixtures(fixtures_dir: Path) -> list[Fixture]:
    """Reload fixtures from disk (8-bit quantised) plus manifest metadata."""
    manifest = json.loads((fixtures_dir / "manifest.json").read_text())
    fixtures: list[Fixture] = []
    for entry in manifest["fixtures"]:
        name = entry["name"]
        gt = entry["ground_truth_shift"]
        fixtures.append(
            Fixture(
                name=name,
                category=entry["category"],
                img_a=_load_png(fixtures_dir / f"{name}_a.png"),
                img_b=_load_png(fixtures_dir / f"{name}_b.png"),
                ground_truth_shift=tuple(gt) if gt is not None else None,
                expected_status=entry["expected_status"],
                expected_reasons=entry["expected_reasons"],
                tolerance_px=entry["tolerance_px"],
                reference_max_shift=entry["reference_max_shift"],
                note=entry["note"],
            )
        )
    return fixtures


if __name__ == "__main__":
    from app.config import get_settings

    out = get_settings().fixtures_dir
    generate_all(out)
    print(f"fixtures written to {out}")
