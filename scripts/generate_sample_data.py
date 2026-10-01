"""Generate sample data files under data/ for first-time users.

Writes a balanced experiment with a known effect and an informative
pre-treatment covariate as both CSV and JSONL, plus a small leakage example.

Usage:
    python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import synthetic

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _write_files(ds, stem: str) -> list[Path]:
    names = [d.name for d in ds.declarations]
    paths: list[Path] = []

    csv_path = DATA_DIR / f"{stem}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["unit_id", "treatment", "outcome", *names])
        for i in range(len(ds.outcome)):
            row = [str(ds.unit_id[i]), int(ds.treatment[i]), f"{ds.outcome[i]:.6f}"]
            for name in names:
                v = ds.covariates[name][i]
                row.append("" if v != v else f"{v:.6f}")
            writer.writerow(row)
    paths.append(csv_path)

    jsonl_path = DATA_DIR / f"{stem}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for i in range(len(ds.outcome)):
            record = {
                "unit_id": str(ds.unit_id[i]),
                "treatment": int(ds.treatment[i]),
                "outcome": float(ds.outcome[i]),
                "covariates": {
                    name: (None if ds.covariates[name][i] != ds.covariates[name][i]
                           else float(ds.covariates[name][i]))
                    for name in names
                },
            }
            fh.write(json.dumps(record) + "\n")
    paths.append(jsonl_path)

    meta = {
        "scenario": ds.scenario,
        "n": len(ds.outcome),
        "true_effect": ds.true_effect,
        "true_beta": ds.true_beta,
        "seed": ds.seed,
        "declarations": [{"name": d.name, "pre_treatment": d.pre_treatment} for d in ds.declarations],
        "params": ds.params,
    }
    meta_path = DATA_DIR / f"{stem}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    paths.append(meta_path)
    return paths


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    balanced = synthetic.generate("balanced", n=2000, true_effect=2.0, beta_pre=3.0, seed=20260927)
    leak = synthetic.generate("leakage", n=1000, true_effect=2.0, seed=20260928)
    for ds, stem in ((balanced, "sample_balanced"), (leak, "sample_leakage")):
        for p in _write_files(ds, stem):
            print(f"wrote {p.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
