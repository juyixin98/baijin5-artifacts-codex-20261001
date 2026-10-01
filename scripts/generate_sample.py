"""Generate committed sample datasets under data/sample/.

Run:  python scripts/generate_sample.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.synth import SyntheticConfig, generate  # noqa: E402


def _write(payload: dict, path: Path) -> None:
    ground_truth = payload.pop("ground_truth")
    path.write_text(json.dumps(payload, indent=2))
    (path.with_suffix(".truth.json")).write_text(json.dumps(ground_truth, indent=2))


def main() -> None:
    out_dir = ROOT / "data" / "sample"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Balanced design, known effect, correlated covariate + leakage + constant.
    _write(generate(SyntheticConfig(n=2000, tau=2.0, beta=3.0, seed=42)),
           out_dir / "balanced.json")

    # Imbalanced allocation (30/70) to stress Welch vs pooled SE.
    _write(generate(SyntheticConfig(n=2000, tau=2.0, beta=3.0,
                                    treatment_prob=0.30, seed=7),
                    add_leakage=False, add_constant=False),
           out_dir / "imbalanced.json")

    # Missing values present (complete-cases / impute policies).
    _write(generate(SyntheticConfig(n=1000, tau=1.5, beta=2.0, seed=99),
                    add_leakage=False, add_constant=False,
                    add_missing=True, missing_fraction=0.05),
           out_dir / "missing.json")

    print(f"sample datasets written to {out_dir}")


if __name__ == "__main__":
    main()
