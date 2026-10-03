"""Offline demo: run the limiter over a fixture and record a reviewable
JSON result (metrics + judgment basis) under results/."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from limiter.config import LimiterConfig
from limiter.fixtures import FIXTURE_BUILDERS, fixture_hash
from limiter.runlog import environment_fingerprint, new_run_id
from limiter.stream import process_offline

RESULTS = Path(__file__).resolve().parent.parent / "results"


def main(fixture_id: str = "impulse") -> None:
    cfg = LimiterConfig()
    fx = FIXTURE_BUILDERS[fixture_id]()
    pcm = fx.pcm()
    res = process_offline(pcm, cfg)
    report = {
        "run_id": new_run_id(),
        "environment": environment_fingerprint(),
        "fixture": {"id": fx.id, "sha256": fixture_hash(pcm), "meta": fx.meta},
        "config": cfg.to_dict(),
        "metrics": {
            "frames": int(res.output.shape[0]),
            "latency_samples": res.latency_samples,
            "input_peak": res.input_peak,
            "output_peak": res.output_peak,
            "promised_ceiling": res.promised_ceiling,
            "ceiling_ok": bool(res.output_peak <= res.promised_ceiling + 1e-12),
            "gain_min": float(res.gain.min()),
        },
    }
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"demo-{fixture_id}-{report['run_id']}.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report["metrics"], indent=2))
    print(f"report -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "impulse")
