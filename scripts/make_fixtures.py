"""Generate the minimal on-disk data fixtures (fixtures/*.npz + manifest).

The tests build signals in memory from limiter.fixtures; this script
materializes the same deterministic signals so reviewers can inspect the
exact bytes and so the API demo can post real files. Re-running always
produces identical hashes.
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from limiter.config import LimiterConfig
from limiter.fixtures import FIXTURE_BUILDERS, fixture_hash

OUT = Path(__file__).resolve().parent.parent / "fixtures"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    cfg = LimiterConfig()
    manifest = {"config": cfg.to_dict(), "fixtures": {}}
    for fid, builder in FIXTURE_BUILDERS.items():
        fx = builder()
        pcm = fx.pcm()
        path = OUT / f"{fid}.npz"
        np.savez_compressed(path, pcm=pcm)
        manifest["fixtures"][fid] = {
            "file": path.name,
            "description": fx.description,
            "meta": fx.meta,
            "sha256": fixture_hash(pcm),
            "frames": int(pcm.shape[0]),
            "channels": int(pcm.shape[1]),
            "input_peak": float(np.max(np.abs(pcm))),
            "promised_ceiling": cfg.promised_ceiling(float(np.max(np.abs(pcm)))),
        }
        print(f"{fid}: {pcm.shape} sha256={manifest['fixtures'][fid]['sha256'][:16]}...")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"manifest -> {OUT / 'manifest.json'}")


if __name__ == "__main__":
    main()
