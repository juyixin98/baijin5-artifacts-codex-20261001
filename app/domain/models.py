"""Internal domain models (framework-independent).

Coordinates are half-open zero-based offsets into the parent sequence:
a fragment covering residues ``sequence[start:end]`` has 1-based residue
positions ``start+1 .. end``. Bonds are indexed 0..n; bond ``i`` lies before
residue ``i`` (bond 0 = protein N boundary, bond n = protein C boundary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .constants import EnzymeRule


class MassStatus(str, Enum):
    """Mass computability state. These are never collapsed into success."""

    DETERMINATE = "DETERMINATE"  # every residue chemically known
    UNCERTAIN = "UNCERTAIN"      # contains B/Z/X (or J) with a mass range
    UNKNOWN = "UNKNOWN"          # unrecognized character, no mass at all


@dataclass(frozen=True)
class MassInterval:
    """Neutral monoisotopic mass, possibly an [min, max] uncertainty band."""

    nominal: float
    min_mass: float
    max_mass: float
    status: MassStatus

    @property
    def is_point(self) -> bool:
        return self.min_mass == self.max_mass


@dataclass(frozen=True)
class ResidueAnnotation:
    position: int          # 1-based in parent sequence
    letter: str
    known: bool
    ambiguous: bool
    mass_min: float
    mass_max: float
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class CleavageSite:
    bond: int
    enzyme: str
    reason: str                       # "after:K" / "before:D"
    terminal_residue_position: int   # 1-based position of the rule residue
    terminal_residue: str
    blocked: bool
    blocking_residue_position: int | None = None
    blocking_residue: str | None = None

    def to_trace(self) -> dict:
        trace = {
            "bond": self.bond,
            "enzyme": self.enzyme,
            "rule": self.reason,
            "rule_residue_position": self.terminal_residue_position,
            "rule_residue": self.terminal_residue,
            "blocked": self.blocked,
        }
        if self.blocked:
            trace["blocking_residue_position"] = self.blocking_residue_position
            trace["blocking_residue"] = self.blocking_residue
        return trace


@dataclass(frozen=True)
class ModificationForm:
    """One concrete modification realization of a fragment."""

    index: int
    delta: float
    placements: tuple[tuple[int, str, str], ...]  # (1-based pos, residue, mod_key)
    nterm_mod: str | None
    cterm_mod: str | None


@dataclass(frozen=True)
class Fragment:
    index: int                         # order in enumeration, 0-based
    start: int
    end: int
    sequence: str
    n_terminal: bool                   # begins at protein N boundary
    c_terminal: bool                   # ends at protein C boundary
    empty: bool
    cleavage_before: int | None        # bond opening this fragment (None => protein N)
    cleavage_after: int | None         # bond closing this fragment (None => protein C)
    missed_cleavages: int
    internal_cleavage_bonds: tuple[int, ...]
    mass: MassInterval
    residues: tuple[ResidueAnnotation, ...]
    modification_forms: tuple[ModificationForm, ...] = field(default_factory=tuple)

    def identity(self) -> tuple:
        """Source identity: equal sequences at different positions differ."""
        return (self.start, self.end, self.n_terminal, self.c_terminal)


@dataclass(frozen=True)
class DigestResult:
    run_id: str
    sequence: str
    sequence_length: int
    enzyme: EnzymeRule
    missed_cleavages: int
    cleavage_sites: tuple[CleavageSite, ...]   # eligible, unblocked bonds
    blocked_sites: tuple[CleavageSite, ...]    # matched rule but blocked
    fragments: tuple[Fragment, ...]
    has_ambiguous: bool
    has_unknown: bool
    mass_uncertain: bool
    warnings: tuple[str, ...]
