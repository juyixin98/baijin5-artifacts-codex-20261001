"""Independent evidence verification.

Given only the public evidence bundle of a finalized round, recompute every
derived artifact from scratch and report whether each check passes:

1. every reveal recomputes to its recorded commitment (binding check);
2. every recorded commitment is accounted for (revealed or listed unrevealed);
3. the seed recomputes from the revealed values;
4. the draw recomputes from the seed and the eligible set;
5. the claimed winner matches the recomputed draw.

This module deliberately does not import the service layer: it re-derives
everything from the evidence alone, so it can be run offline against an
exported evidence JSON (see ``scripts/verify_evidence.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from commit_reveal.crypto.commitment import combine_seed, compute_commitment
from commit_reveal.crypto.draw import DRAW_ALGORITHM, draw_index
from commit_reveal.protocol.encoding import EVIDENCE_VERSION


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class VerifyReport:
    valid: bool
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks
            ],
        }


def verify_evidence(evidence: dict) -> VerifyReport:
    checks: list[Check] = []

    def check(name: str, ok: bool, detail: str) -> bool:
        checks.append(Check(name, ok, detail))
        return ok

    # Structural checks first; abort early if the bundle is not parseable.
    try:
        version = evidence["version"]
        round_id = evidence["round_id"]
        commitments = evidence["commitments"]
        reveals = evidence["reveals"]
        unrevealed = evidence["unrevealed_commitments"]
        seed = evidence["seed"]
        draw = evidence["draw"]
    except KeyError as exc:
        check("structure", False, f"missing field: {exc}")
        return VerifyReport(valid=False, checks=checks)
    check("structure", True, "all required fields present")

    if not check("version", version == EVIDENCE_VERSION,
                 f"expected {EVIDENCE_VERSION}, got {version}"):
        return VerifyReport(valid=False, checks=checks)

    # 1. Binding: each reveal must recompute to its recorded commitment.
    committed = {c["participant_id"]: c["commitment"] for c in commitments}
    binding_ok = True
    for reveal in reveals:
        pid = reveal["participant_id"]
        recorded = committed.get(pid)
        if recorded is None:
            check("binding", False, f"reveal by {pid!r} has no recorded commitment")
            binding_ok = False
            continue
        recomputed = compute_commitment(
            round_id, pid,
            bytes.fromhex(reveal["random_value"]), bytes.fromhex(reveal["salt"]),
        )
        if recomputed != recorded:
            check("binding", False, f"reveal by {pid!r} does not match its commitment")
            binding_ok = False
    if binding_ok:
        check("binding", True, f"all {len(reveals)} reveals match their commitments")

    # 2. Completeness: commitments = revealed ∪ unrevealed, disjoint.
    revealed_pids = {r["participant_id"] for r in reveals}
    unrevealed_set = set(unrevealed)
    complete = (
        set(committed) == revealed_pids | unrevealed_set
        and not (revealed_pids & unrevealed_set)
    )
    check("completeness", complete,
          "every commitment is either revealed or listed unrevealed, exclusively")

    # 3. Seed recomputation.
    try:
        recomputed_seed = combine_seed(
            round_id,
            [(r["participant_id"], bytes.fromhex(r["random_value"])) for r in reveals],
        )
        seed_ok = recomputed_seed == seed
    except (ValueError, KeyError) as exc:
        recomputed_seed = None
        seed_ok = False
    check("seed", seed_ok,
          "seed recomputed from reveals" if seed_ok else "seed mismatch or underivable")

    # 4 + 5. Draw recomputation and winner.
    draw_ok = False
    winner_ok = False
    if seed_ok:
        eligible = draw["eligible"]
        if draw.get("algorithm") != DRAW_ALGORITHM:
            check("draw", False, f"unknown algorithm {draw.get('algorithm')!r}")
        elif sorted(eligible) != sorted(revealed_pids):
            check("draw", False, "eligible set is not exactly the revealed participants")
        else:
            recomputed_index = draw_index(seed, len(eligible))
            draw_ok = recomputed_index == draw["winner_index"]
            check("draw", draw_ok,
                  f"winner_index={draw['winner_index']} recomputed ok"
                  if draw_ok else
                  f"claimed index {draw['winner_index']} != recomputed {recomputed_index}")
            winner_ok = draw_ok and eligible[draw["winner_index"]] == draw["winner"]
            check("winner", winner_ok,
                  f"winner={draw['winner']}" if winner_ok else "winner mismatch")
    else:
        check("draw", False, "skipped: seed invalid")
        check("winner", False, "skipped: seed invalid")

    return VerifyReport(valid=all(c.ok for c in checks), checks=checks)
