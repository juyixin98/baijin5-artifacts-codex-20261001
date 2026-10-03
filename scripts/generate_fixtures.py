"""Populate local profile fixtures.

Copies a fixed set of ICC profiles from well-known local system locations
(explicit local dependencies, no network) into ``profiles/`` and writes
deterministic corrupt-profile fixtures into ``tests/fixtures/bad/``.

Run:  python scripts/generate_fixtures.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO_ROOT / "profiles"
BAD_DIR = REPO_ROOT / "tests" / "fixtures" / "bad"

# name in registry -> absolute source path (local system dependency)
SYSTEM_PROFILES = {
    "sRGB.icc": "/usr/share/color/icc/colord/sRGB.icc",
    "AdobeRGB1998.icc": "/usr/share/color/icc/colord/AdobeRGB1998.icc",
    "SWOP_TR003_coated_3.icc": "/usr/share/color/icc/colord/SWOP_TR003_coated_3.icc",
    "SwappedRedAndGreen.icc": "/usr/share/color/icc/colord/SwappedRedAndGreen.icc",
    "sgray.icc": "/usr/share/color/icc/ghostscript/sgray.icc",
}

# Valid ICC files that must still be rejected (named-color class).
REJECT_PROFILES = {
    "named_color.icc": "/usr/share/color/icc/colord/x11-colors.icc",
}

SOURCES_MD = """# Profile provenance

All profiles in this directory are unmodified copies of ICC profiles
installed locally on the build machine (Ubuntu `colord` / `ghostscript`
packages). They are test/development fixtures, not production data.

| file | source package path |
| --- | --- |
{rows}
"""


def main() -> None:
    PROFILES_DIR.mkdir(exist_ok=True)
    BAD_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for name, src in SYSTEM_PROFILES.items():
        shutil.copyfile(src, PROFILES_DIR / name)
        rows.append(f"| {name} | {src} |")
    for name, src in REJECT_PROFILES.items():
        shutil.copyfile(src, BAD_DIR / name)

    srgb = (PROFILES_DIR / "sRGB.icc").read_bytes()
    (BAD_DIR / "truncated.icc").write_bytes(srgb[:100])
    (BAD_DIR / "garbage.icc").write_bytes(bytes((i * 37 + 11) % 256 for i in range(512)))

    (PROFILES_DIR / "SOURCES.md").write_text(SOURCES_MD.format(rows="\n".join(rows)))
    print(f"profiles: {sorted(p.name for p in PROFILES_DIR.glob('*.icc'))}")
    print(f"bad fixtures: {sorted(p.name for p in BAD_DIR.iterdir())}")


if __name__ == "__main__":
    main()
