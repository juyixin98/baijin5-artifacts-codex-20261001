#!/usr/bin/env python3
"""Dump the deterministic fixture bundle to ``fixtures/*.npz`` for reuse
outside Python (e.g. plotting, cross-language checks)."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fixtures import fixture_bundle

OUT_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for name, data in fixture_bundle().items():
        path = OUT_DIR / f"{name}.npz"
        np.savez_compressed(path, samples=data)
        print(f"wrote {path} ({data.size} samples)")


if __name__ == "__main__":
    main()
