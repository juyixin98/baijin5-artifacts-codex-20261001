"""Digest enumeration: bond scan, missed-cleavage merging, provenance.

Fragment model
--------------
Fragments are stored by **position**, never deduplicated by sequence: two
fragments with identical strings at different locations get distinct
``fragment_id`` values and distinct provenance.

Boundaries are half-open residue offsets ``0..n`` (see :mod:`app.domain.parsing`).
A cut at bond ``i`` (between residues ``i`` and ``i+1``) inserts boundary ``i``.
Pieces are consecutive boundary pairs:

    piece residues = [boundary[t] + 1, boundary[t+1]]   (1-based, inclusive)

Zero-length pieces (coincident boundaries) are **suppressed uniformly** and
counted in ``empty_fragments_suppressed`` — with internal bond cuts distinct
bond indices never coincide, so for valid input this count is zero, but the
guard makes the N/C-terminal and custom-cut-set cases behave identically.

Missed cleavages
----------------
``missed_cleavages = k`` enumerates every contiguous union of ``1..k+1``
adjacent primary fragments. A union spanning ``j - i`` internal cut bonds
carries ``missed_cleavages = j - i``. ``k = 0`` is the complete digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.mass import MassResult, compute_mass_batch
from app.domain.rules import BondDecision, Enzyme
from app.logging_setup import RunLogger


@dataclass(frozen=True)
class Fragment:
    fragment_id: str
    order: int
    sequence: str
    start: int  # 1-based inclusive
    end: int  # 1-based inclusive
    length: int
    n_term_offset: int  # half-open left boundary (0 = protein N-terminus)
    c_term_offset: int  # half-open right boundary (n = protein C-terminus)
    missed_cleavages: int
    spanned_cut_bonds: tuple[int, ...]
    primary_fragment_indices: tuple[int, ...]
    is_nterminal: bool
    is_cterminal: bool
    mass: MassResult

    def to_dict(self) -> dict:
        return {
            "fragment_id": self.fragment_id,
            "order": self.order,
            "sequence": self.sequence,
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "n_term_offset": self.n_term_offset,
            "c_term_offset": self.c_term_offset,
            "missed_cleavages": self.missed_cleavages,
            "spanned_cut_bonds": list(self.spanned_cut_bonds),
            "primary_fragment_indices": list(self.primary_fragment_indices),
            "is_nterminal": self.is_nterminal,
            "is_cterminal": self.is_cterminal,
            "mass": self.mass.to_dict(),
        }


@dataclass(frozen=True)
class DigestResult:
    sequence: str
    sequence_length: int
    enzyme: str
    missed_cleavages: int
    cut_bonds: tuple[int, ...]
    blocked_bonds: tuple[int, ...]
    decisions: tuple[BondDecision, ...]
    fragments: tuple[Fragment, ...]
    empty_fragments_suppressed: int
    primary_fragment_count: int

    def fragment_dicts(self) -> list[dict]:
        return [f.to_dict() for f in self.fragments]

    def to_dict(self) -> dict:
        return {
            "sequence_length": self.sequence_length,
            "enzyme": self.enzyme,
            "missed_cleavages": self.missed_cleavages,
            "cut_bonds": list(self.cut_bonds),
            "blocked_bonds": list(self.blocked_bonds),
            "empty_fragments_suppressed": self.empty_fragments_suppressed,
            "primary_fragment_count": self.primary_fragment_count,
            "fragment_count": len(self.fragments),
            "bond_decisions": [
                {
                    "bond": d.bond,
                    "p1": d.p1,
                    "p1_prime": d.p1_prime,
                    "decision": d.decision,
                    "reason": d.reason,
                    "matched_rule": d.matched_rule,
                }
                for d in self.decisions
            ],
            "fragments": self.fragment_dicts(),
        }


def _scan_bonds(seq: str, enzyme: Enzyme, run_log: RunLogger | None) -> tuple[BondDecision, ...]:
    decisions: list[BondDecision] = []
    for bond in range(1, len(seq)):
        decision = enzyme.evaluate_bond(seq, bond)
        decisions.append(decision)
        if run_log is not None:
            run_log.judgment(
                "bond_evaluated",
                f"bond {bond}: {decision.p1}|{decision.p1_prime} -> {decision.decision}",
                bond=bond,
                p1=decision.p1,
                p1_prime=decision.p1_prime,
                decision=decision.decision,
                reason=decision.reason,
                matched_rule=decision.matched_rule,
                progress=f"{bond}/{len(seq) - 1}",
            )
    return tuple(decisions)


def digest(
    seq: str,
    enzyme: Enzyme,
    missed_cleavages: int,
    mass_decimals: int = 6,
    run_log: RunLogger | None = None,
) -> DigestResult:
    """Enumerate all digest fragments with positions and mature masses."""
    if missed_cleavages < 0:
        # Defensive: API layer validates with a categorized error first.
        raise ValueError("missed_cleavages must be >= 0")

    n = len(seq)
    decisions = _scan_bonds(seq, enzyme, run_log)
    cut_bonds = tuple(d.bond for d in decisions if d.decision == "CUT")
    blocked_bonds = tuple(d.bond for d in decisions if d.decision == "BLOCKED")

    # Half-open boundaries: N-terminus 0, every cut bond, C-terminus n.
    boundaries = [0, *cut_bonds, n]

    # Primary fragments with uniform empty-piece suppression.
    empty_suppressed = 0
    primary: list[tuple[int, int]] = []  # (left_offset, right_offset)
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        if right <= left:
            empty_suppressed += 1
            if run_log is not None:
                run_log.step(
                    "empty_fragment_suppressed",
                    f"zero-length piece at boundary {left} suppressed",
                    boundary=left,
                )
            continue
        primary.append((left, right))

    primary_sequences = [seq[l:r] for l, r in primary]
    masses = compute_mass_batch(primary_sequences, decimals=mass_decimals)

    # Enumerate contiguous unions of primary fragments (0..k missed cuts).
    fragments: list[Fragment] = []
    order = 0
    total_primary = len(primary)
    for i in range(total_primary):
        max_j = min(total_primary - 1, i + missed_cleavages)
        for j in range(i, max_j + 1):
            left_offset = primary[i][0]
            right_offset = primary[j][1]
            spanned = tuple(
                cut for cut in cut_bonds if left_offset < cut < right_offset
            )
            merged_seq = seq[left_offset:right_offset]
            # Recompute the union mass (ambiguity/unknown status is per-union).
            union_mass = compute_mass_batch([merged_seq], decimals=mass_decimals)[0]
            order += 1
            fragments.append(
                Fragment(
                    fragment_id=f"F{order:04d}",
                    order=order,
                    sequence=merged_seq,
                    start=left_offset + 1,
                    end=right_offset,
                    length=right_offset - left_offset,
                    n_term_offset=left_offset,
                    c_term_offset=right_offset,
                    missed_cleavages=j - i,
                    spanned_cut_bonds=spanned,
                    primary_fragment_indices=tuple(range(i + 1, j + 2)),
                    is_nterminal=left_offset == 0,
                    is_cterminal=right_offset == n,
                    mass=union_mass,
                )
            )
            if run_log is not None:
                run_log.progress(
                    "fragment_emitted",
                    f"fragment F{order:04d} residues [{left_offset + 1},{right_offset}] "
                    f"missed={j - i} mass_status={union_mass.status}",
                    fragment_id=f"F{order:04d}",
                    start=left_offset + 1,
                    end=right_offset,
                    missed_cleavages=j - i,
                    mass_status=union_mass.status,
                )

    result = DigestResult(
        sequence=seq,
        sequence_length=n,
        enzyme=enzyme.name,
        missed_cleavages=missed_cleavages,
        cut_bonds=cut_bonds,
        blocked_bonds=blocked_bonds,
        decisions=decisions,
        fragments=tuple(fragments),
        empty_fragments_suppressed=empty_suppressed,
        primary_fragment_count=total_primary,
    )
    if run_log is not None:
        run_log.step(
            "digest_complete",
            f"{len(cut_bonds)} cuts, {total_primary} primary fragments, "
            f"{len(fragments)} emitted (0..{missed_cleavages} missed)",
            cut_count=len(cut_bonds),
            blocked_count=len(blocked_bonds),
            primary_fragment_count=total_primary,
            fragment_count=len(fragments),
            empty_suppressed=empty_suppressed,
        )
    return result
