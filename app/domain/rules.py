"""Explicit enzymatic cleavage rules with blocking context.

Rule semantics
--------------
A bond ``i`` joins residue ``i`` (P1) to residue ``i+1`` (P1'). It is a
candidate when P1 is in the enzyme's ``cut_after`` set. A candidate is
**blocked** when *any* blocking rule matches:

* ``not_before``  : do not cut when P1' is in this set (classic ProLINE rule:
  trypsin does not cleave Lys/Arg-Pro).
* ``not_after``   : do not cut when P1 is in this set *even if* it also appears
  in ``cut_after`` (explicit override; rare, kept for synthetic enzymes).
* ``require_nterm`` / ``require_cterm`` : cut only at the sequence N- or
  C-terminal boundary (used by synthetic terminalases; mutually exclusive with
  ``cut_after``).

Terminal boundaries are never "internal bonds": there is no bond 0 and no bond
``n``, so a digest of a single residue yields exactly one fragment.

Every bond evaluation returns a :class:`BondDecision` carrying the evidence
(P1, P1', matched rule) — this is what logs and provenance records store.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from app.errors import InvalidRuleError

DecisionKind = Literal["CUT", "BLOCKED", "NO_MATCH", "TERMINAL"]


@dataclass(frozen=True)
class BondDecision:
    bond: int  # 1-based index of P1; range 1..n-1
    position_p1: int
    p1: str
    p1_prime: str
    decision: DecisionKind
    reason: str
    matched_rule: str | None = None


@dataclass(frozen=True)
class Enzyme:
    name: str
    cut_after: frozenset[str] = field(default_factory=frozenset)
    not_before: frozenset[str] = field(default_factory=frozenset)
    not_after: frozenset[str] = field(default_factory=frozenset)
    require_nterm: bool = False
    require_cterm: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not (self.cut_after or self.require_nterm or self.require_cterm):
            raise InvalidRuleError(
                f"enzyme {self.name!r} must declare cut_after or a terminal rule",
                {"enzyme": self.name},
            )
        if (self.require_nterm or self.require_cterm) and self.cut_after:
            raise InvalidRuleError(
                f"enzyme {self.name!r} mixes terminal-only with cut_after",
                {"enzyme": self.name},
            )
        if self.require_nterm and self.require_cterm:
            raise InvalidRuleError(
                f"enzyme {self.name!r} cannot require both termini",
                {"enzyme": self.name},
            )

    @classmethod
    def from_dict(cls, raw: dict) -> "Enzyme":
        try:
            name = raw["name"]
        except KeyError as exc:
            raise InvalidRuleError("enzyme entry missing 'name'", {"raw": raw}) from exc

        def chars(key: str) -> frozenset[str]:
            value = raw.get(key, "")
            if isinstance(value, str):
                tokens = [c for c in value.upper() if c.isalpha()]
            elif isinstance(value, list):
                tokens = [str(c).upper() for c in value]
            else:
                raise InvalidRuleError(
                    f"enzyme {name!r} field {key!r} must be string or list",
                    {"enzyme": name, "field": key},
                )
            return frozenset(tokens)

        return cls(
            name=name,
            cut_after=chars("cut_after"),
            not_before=chars("not_before"),
            not_after=chars("not_after"),
            require_nterm=bool(raw.get("require_nterm", False)),
            require_cterm=bool(raw.get("require_cterm", False)),
            description=str(raw.get("description", "")),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "cut_after": "".join(sorted(self.cut_after)),
            "not_before": "".join(sorted(self.not_before)),
            "not_after": "".join(sorted(self.not_after)),
            "require_nterm": self.require_nterm,
            "require_cterm": self.require_cterm,
            "description": self.description,
        }

    def evaluate_bond(self, seq: str, bond: int) -> BondDecision:
        """Evaluate internal bond ``bond`` (1-based P1 index, 1 <= bond < n)."""
        p1 = seq[bond - 1]
        p1_prime = seq[bond]
        if self.require_nterm:
            if bond == 1:
                return BondDecision(bond, bond, p1, p1_prime, "CUT",
                                    "N-terminal-only rule", "require_nterm")
            return BondDecision(bond, bond, p1, p1_prime, "NO_MATCH",
                                "terminal-only enzyme; bond not at N-terminus")
        if self.require_cterm:
            if bond == len(seq) - 1:
                return BondDecision(bond, bond, p1, p1_prime, "CUT",
                                    "C-terminal-only rule", "require_cterm")
            return BondDecision(bond, bond, p1, p1_prime, "NO_MATCH",
                                "terminal-only enzyme; bond not at C-terminus")

        if p1 in self.not_after:
            return BondDecision(bond, bond, p1, p1_prime, "BLOCKED",
                                f"P1 {p1} explicitly listed in not_after",
                                "not_after")
        if p1 not in self.cut_after:
            return BondDecision(bond, bond, p1, p1_prime, "NO_MATCH",
                                f"P1 {p1} not in cut_after")
        if p1_prime in self.not_before:
            return BondDecision(bond, bond, p1, p1_prime, "BLOCKED",
                                f"P1' {p1_prime} blocks cleavage (not_before)",
                                "not_before")
        return BondDecision(bond, bond, p1, p1_prime, "CUT",
                            f"P1 {p1} in cut_after and no blocking context",
                            "cut_after")


# ---------------------------------------------------------------------------
# Bundled synthetic enzyme catalog (local fixture; no external accounts).
# ---------------------------------------------------------------------------

BUILTIN_ENZYMES: tuple[Enzyme, ...] = (
    Enzyme(
        name="trypsin_syn",
        cut_after=frozenset("KR"),
        not_before=frozenset("P"),
        description="Synthetic trypsin: cut after K/R unless followed by P.",
    ),
    Enzyme(
        name="trypsin_no_proline_rule",
        cut_after=frozenset("KR"),
        description="Trypsin without the KP/RP blocking context.",
    ),
    Enzyme(
        name="chymotrypsin_syn",
        cut_after=frozenset("FYWL"),
        not_before=frozenset("P"),
        description="Synthetic chymotrypsin: cut after F/Y/W/L unless P follows.",
    ),
    Enzyme(
        name="lys-c_syn",
        cut_after=frozenset("K"),
        not_before=frozenset("P"),
        description="Synthetic Lys-C: cut after K unless P follows.",
    ),
    Enzyme(
        name="cnbr_syn",
        cut_after=frozenset("M"),
        description="Synthetic CNBr mimic: cut after M (no blocking context).",
    ),
    Enzyme(
        name="arg-c_syn",
        cut_after=frozenset("R"),
        not_before=frozenset("P"),
        description="Synthetic Arg-C: cut after R unless P follows.",
    ),
    Enzyme(
        name="nterm_cut_syn",
        require_nterm=True,
        description="Synthetic terminalase: single cut at the N-terminal bond.",
    ),
    Enzyme(
        name="cterm_cut_syn",
        require_cterm=True,
        description="Synthetic terminalase: single cut at the C-terminal bond.",
    ),
)


def load_enzyme_table(path: str) -> dict[str, Enzyme]:
    """Load an enzyme table JSON file, falling back to the bundled catalog."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return {enzyme.name: enzyme for enzyme in BUILTIN_ENZYMES}
    entries = raw.get("enzymes", raw if isinstance(raw, list) else [])
    table: dict[str, Enzyme] = {}
    for entry in entries:
        enzyme = Enzyme.from_dict(entry)
        table[enzyme.name] = enzyme
    return table
