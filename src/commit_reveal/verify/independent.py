"""Independent verification of public evidence.

Given only the evidence document produced at finalize time, recompute every
derived value — each commitment, the commitment-set hash, the seed, the
ranking, the winner — and report per-check PASS/FAIL. This module shares the
canonical encoding and crypto adapters with the server (they are the public
spec), but it never reads the server's database; the evidence JSON is the
sole input, so anyone holding it can replay the draw offline:

    python -m commit_reveal.verify.independent evidence.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from commit_reveal.crypto import commitment as commit_mod
from commit_reveal.crypto import seed as seed_mod
from commit_reveal.draw.deterministic import deterministic_ranking


@dataclass
class Check:
    name: str
    passed: bool
    expected: object
    actual: object


def verify_evidence(evidence: dict) -> list[Check]:
    checks: list[Check] = []
    round_id = evidence["round_id"]

    # 1. Every reveal must reproduce its stored commitment.
    for reveal in evidence["reveals"]:
        recomputed = commit_mod.compute_commitment(
            round_id,
            reveal["participant_id"],
            bytes.fromhex(reveal["value"]),
            bytes.fromhex(reveal["salt"]),
        )
        stored = next(
            (
                c["commitment"]
                for c in evidence["commitments"]
                if c["participant_id"] == reveal["participant_id"]
            ),
            None,
        )
        checks.append(
            Check(
                f"commitment[{reveal['participant_id']}]",
                stored is not None and recomputed == stored,
                stored,
                recomputed,
            )
        )

    # 2. Commitment-set hash.
    set_hash = commit_mod.commitment_set_hash(
        [(c["participant_id"], c["commitment"]) for c in evidence["commitments"]]
    )
    checks.append(
        Check("commitment_set_hash", set_hash == evidence["commitment_set_hash"],
              evidence["commitment_set_hash"], set_hash)
    )

    # 3. Aborted rounds carry no seed/draw; nothing further to recompute.
    if evidence["aborted"]:
        checks.append(Check("aborted", evidence["seed"] is None, None,
                            evidence["seed"]))
        return checks

    # 4. Seed from the revealed values.
    seed = seed_mod.derive_seed(
        round_id, [bytes.fromhex(r["value"]) for r in evidence["reveals"]]
    )
    checks.append(
        Check("seed", seed.hex() == evidence["seed"], evidence["seed"], seed.hex())
    )

    # 5. Ranking and winner from the seed over the eligible set.
    ranking = deterministic_ranking(evidence["draw"]["eligible"], seed)
    checks.append(
        Check("ranking", ranking == evidence["draw"]["ranking"],
              evidence["draw"]["ranking"], ranking)
    )
    checks.append(
        Check("winner", ranking[0] == evidence["draw"]["winner"],
              evidence["draw"]["winner"], ranking[0])
    )

    # 6. Eligible set must equal the revealed participants, sorted.
    eligible = sorted(r["participant_id"] for r in evidence["reveals"])
    checks.append(
        Check("eligible", eligible == evidence["draw"]["eligible"],
              evidence["draw"]["eligible"], eligible)
    )
    return checks


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m commit_reveal.verify.independent EVIDENCE.json",
              file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        evidence = json.load(fh)
    checks = verify_evidence(evidence)
    failed = 0
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        if not check.passed:
            failed += 1
        print(f"[{status}] {check.name}")
        if not check.passed:
            print(f"    expected: {check.expected}")
            print(f"    actual:   {check.actual}")
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
