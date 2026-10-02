"""Loading of local synthetic fixtures (PNG pairs + ground-truth manifest)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "data"
MANIFEST_NAME = "manifest.json"


class FixtureNotFound(KeyError):
    pass


def manifest_path(fixtures_dir: Path = FIXTURES_DIR) -> Path:
    return fixtures_dir / MANIFEST_NAME


def load_manifest(fixtures_dir: Path = FIXTURES_DIR) -> dict:
    path = manifest_path(fixtures_dir)
    if not path.exists():
        raise FixtureNotFound(
            f"fixture manifest not found at {path}; run fixtures/generate_fixtures.py")
    return json.loads(path.read_text())


def list_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> list[dict]:
    manifest = load_manifest(fixtures_dir)
    return [{"id": k, **{kk: vv for kk, vv in v.items()}}
            for k, v in manifest["fixtures"].items()]


def load_fixture_pair(fixture_id: str, fixtures_dir: Path = FIXTURES_DIR
                      ) -> tuple[np.ndarray, np.ndarray, dict]:
    manifest = load_manifest(fixtures_dir)
    entry = manifest["fixtures"].get(fixture_id)
    if entry is None:
        raise FixtureNotFound(fixture_id)
    ref = np.asarray(Image.open(fixtures_dir / entry["reference"]), dtype=np.float64)
    mov = np.asarray(Image.open(fixtures_dir / entry["moving"]), dtype=np.float64)
    return ref, mov, entry
