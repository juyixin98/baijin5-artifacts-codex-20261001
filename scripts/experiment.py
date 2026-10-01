"""Reproducible experiment: enumerate every flip on small paired datasets.

Run (from a clean checkout):

    PYTHONPATH=. python3 scripts/experiment.py

The script uses only local synthetic fixtures and writes a JSON report to
``data/experiment_report.json`` plus a human-readable summary to stdout.  It
covers:
  * identical outcomes,
  * an extreme-difference dataset,
  * the genuinely disconnected acceptance-set dataset,
  * an "approximate replay" dataset that exceeds the enumeration budget and
    is therefore answered by Monte Carlo twice (the two replays must agree).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import __version__  # noqa: E402
from app.config import SETTINGS  # noqa: E402
from app.evidence import EvidenceRecord  # noqa: E402
from app.service import InferenceService  # noqa: E402

FIXTURES = {
    "identical_outcomes": {
        "treated": [5, 5, 5, 5],
        "control": [5, 5, 5, 5],
        "note": "all differences zero: null effect is never rejected",
    },
    "extreme_differences": {
        "treated": [15, -5, 15, -5],
        "control": [5, 5, 5, 5],  # d = [10,-10,10,-10]
        "note": "observed statistic is zero -> two-sided p = 1",
    },
    "disconnected_acceptance": {
        "treated": [0, -3, 3, -3, 0],
        "control": [1, 1, 1, 1, 1],  # d = [-1,-4,2,-4,-1]
        "note": "probability-ordering acceptance set has three components",
    },
}


def main() -> int:
    service = InferenceService(SETTINGS)
    report = {"service_version": __version__, "settings": {
        "exact_budget": SETTINGS.exact_budget,
        "crossing_budget": SETTINGS.crossing_budget,
        "mc_draws": SETTINGS.mc_draws,
        "mc_seed": SETTINGS.mc_seed,
    }, "cases": []}

    for name, fx in FIXTURES.items():
        evidence = EvidenceRecord()
        pv_payload = {"treated": fx["treated"], "control": fx["control"],
                      "method": "two_sided_prob" if name == "disconnected_acceptance" else "two_sided_abs",
                      "tau": 0.0, "alpha": 0.1}
        pv = service.p_value(pv_payload, evidence)
        inv_ev = EvidenceRecord()
        inv = service.invert({**pv_payload, "alpha": 0.1}, inv_ev)
        case = {
            "name": name,
            "note": fx["note"],
            "zero_effect_p_value": pv["p_value"],
            "p_value_kind": pv["kind"],
            "acceptance_components": [c["rendered"] for c in inv["components"]],
            "certified": inv["certified"],
            "disconnected": inv["is_disconnected"],
            "request_ids": [evidence.request_id, inv_ev.request_id],
            "uncertainties": inv["uncertainty"],
        }
        report["cases"].append(case)
        print(f"[{name}] {fx['note']}")
        print(f"  zero-effect p = {case['zero_effect_p_value']:.6g} ({case['p_value_kind']})")
        print(f"  acceptance set = {' U '.join(case['acceptance_components']) or 'EMPTY'}")
        print(f"  certified={case['certified']} disconnected={case['disconnected']}")

    # approximate replay: force a large paired design through Monte Carlo
    n_pairs = 20  # 2**20 = 1,048,576 > default exact budget
    treated = [((i * 37) % 13) - 6 for i in range(n_pairs)]
    control = [0] * n_pairs
    replay = []
    for run in range(2):
        ev = EvidenceRecord()
        r = service.p_value(
            {"treated": treated, "control": control, "method": "two_sided_abs", "tau": 0.0}, ev
        )
        replay.append({"request_id": ev.request_id, "p_value": r["p_value"],
                       "error": r["monte_carlo_error"], "n_evaluated": r["n_evaluated"]})
    reproducible = replay[0]["p_value"] == replay[1]["p_value"]
    report["approximate_replay"] = {"runs": replay, "reproducible": reproducible}
    print(f"[approximate_replay] p={replay[0]['p_value']:.6g} +/- {replay[0]['error']:.4g}; "
          f"two runs identical = {reproducible}")

    out = Path(__file__).resolve().parent.parent / "data" / "experiment_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nreport written to {out}")
    return 0 if reproducible else 1


if __name__ == "__main__":
    raise SystemExit(main())
