"""Domain constants: atomic masses, residues, enzyme rules, modifications.

Residue monoisotopic masses are *derived* from integer molecular formulas
(free amino acid minus H2O), so the table cannot silently drift from the
stoichiometry used by the independent mass cross-check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Monoisotopic atomic masses (Da), neutral isotope abundances.
# Source: AME2020 / NIST atomic mass tables (commonly used proteomics values).
# ---------------------------------------------------------------------------
ELEMENT_MASSES: dict[str, float] = {
    "H": 1.0078250319,
    "C": 12.0,
    "N": 14.003074004,
    "O": 15.99491462,
    "S": 31.972071174,
    "P": 30.973762,
    "Se": 79.9165218,
}
WATER_MASS = 2.0 * ELEMENT_MASSES["H"] + ELEMENT_MASSES["O"]
PROTON_MASS = 1.0072764666

# Formula element ordering used everywhere.
ELEMENT_ORDER = ("C", "H", "N", "O", "S", "P", "Se")
Formula = tuple[int, int, int, int, int, int, int]
WATER_FORMULA: Formula = (0, 2, 0, 1, 0, 0, 0)


def formula_mass(formula: Formula) -> float:
    return sum(count * ELEMENT_MASSES[el] for count, el in zip(formula, ELEMENT_ORDER))


@dataclass(frozen=True)
class Residue:
    letter: str
    name: str
    formula: Formula  # neutral free amino acid molecular formula
    ambiguous: bool = False  # letter denotes more than one chemical identity

    @property
    def residue_mass(self) -> float:
        """Monoisotopic residue mass = free amino acid - H2O."""
        return formula_mass(self.formula) - WATER_MASS


def _f(c: int, h: int, n: int, o: int, s: int = 0, se: int = 0) -> Formula:
    return (c, h, n, o, s, 0, se)


# 20 canonical amino acids + selenocysteine (U), all chemically determinate.
RESIDUES: dict[str, Residue] = {
    "G": Residue("G", "Glycine", _f(2, 5, 1, 2)),
    "A": Residue("A", "Alanine", _f(3, 7, 1, 2)),
    "S": Residue("S", "Serine", _f(3, 7, 1, 3)),
    "P": Residue("P", "Proline", _f(5, 9, 1, 2)),
    "V": Residue("V", "Valine", _f(5, 11, 1, 2)),
    "T": Residue("T", "Threonine", _f(4, 9, 1, 3)),
    "C": Residue("C", "Cysteine", _f(3, 7, 1, 2, 1)),
    "U": Residue("U", "Selenocysteine", _f(3, 7, 1, 2, se=1)),
    "L": Residue("L", "Leucine", _f(6, 13, 1, 2)),
    "I": Residue("I", "Isoleucine", _f(6, 13, 1, 2)),
    "N": Residue("N", "Asparagine", _f(4, 8, 2, 3)),
    "D": Residue("D", "Aspartic acid", _f(4, 7, 1, 4)),
    "Q": Residue("Q", "Glutamine", _f(5, 10, 2, 3)),
    "K": Residue("K", "Lysine", _f(6, 14, 2, 2)),
    "E": Residue("E", "Glutamic acid", _f(5, 9, 1, 4)),
    "M": Residue("M", "Methionine", _f(5, 11, 1, 2, 1)),
    "H": Residue("H", "Histidine", _f(6, 9, 3, 2)),
    "F": Residue("F", "Phenylalanine", _f(9, 11, 1, 2)),
    "R": Residue("R", "Arginine", _f(6, 14, 4, 2)),
    "Y": Residue("Y", "Tyrosine", _f(9, 11, 1, 3)),
    "W": Residue("W", "Tryptophan", _f(11, 12, 2, 2)),
}

# ---------------------------------------------------------------------------
# Ambiguous letters: the residue identity is unknown even when the mass range
# happens to collapse (J = I or L, both isobaric). Kept distinct from truly
# unsupported characters: B/Z/J/X are parseable, other symbols are not.
# Bounds are inclusive min/max residue masses over the candidate identities.
# ---------------------------------------------------------------------------

AmbiguityKind = Literal["B", "Z", "J", "X"]


@dataclass(frozen=True)
class AmbiguousResidue:
    letter: str
    candidates: tuple[str, ...]
    description: str

    @property
    def min_mass(self) -> float:
        return min(RESIDUES[c].residue_mass for c in self.candidates)

    @property
    def max_mass(self) -> float:
        return max(RESIDUES[c].residue_mass for c in self.candidates)

    @property
    def mass_determinate(self) -> bool:
        return self.min_mass == self.max_mass


AMBIGUOUS_RESIDUES: dict[str, AmbiguousResidue] = {
    "B": AmbiguousResidue("B", ("N", "D"), "Asn or Asp"),
    "Z": AmbiguousResidue("Z", ("Q", "E"), "Gln or Glu"),
    "J": AmbiguousResidue("J", ("I", "L"), "Ile or Leu (isobaric)"),
    # X = any of the 20 canonical residues; U excluded by convention.
    "X": AmbiguousResidue(
        "X",
        tuple(sorted(c for c in RESIDUES if c != "U")),
        "any canonical residue",
    ),
}

KNOWN_LETTERS = frozenset(RESIDUES) | frozenset(AMBIGUOUS_RESIDUES)


# ---------------------------------------------------------------------------
# Enzyme / chemical cleavage rules.
#
# A cleavage bond sits *between* residues, indexed 0..n where bond 0 is the
# protein N-side boundary and bond n the C-side boundary.
#   cleave_after : bond after residue i may cut when residue i is in the set
#   cleave_before: bond before residue j may cut when residue j is in the set
# Blocking context is explicit and directional:
#   cterm_block : for an after-rule, the residue on the bond's C side blocks
#   nterm_block : for a before-rule, the residue on the bond's N side blocks
# Protein-boundary bonds have no residue on the outer side: that side can never
# match a blocking set, so e.g. Asp-N on an N-terminal D really does create an
# explicit empty N-terminal segment instead of being silently dropped.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnzymeRule:
    key: str
    name: str
    cleave_after: frozenset[str] = field(default_factory=frozenset)
    cleave_before: frozenset[str] = field(default_factory=frozenset)
    cterm_block: frozenset[str] = field(default_factory=frozenset)
    nterm_block: frozenset[str] = field(default_factory=frozenset)
    description: str = ""
    source_note: str = ""

    def to_echo(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "cleave_after": "".join(sorted(self.cleave_after)),
            "cleave_before": "".join(sorted(self.cleave_before)),
            "cterm_block": "".join(sorted(self.cterm_block)),
            "nterm_block": "".join(sorted(self.nterm_block)),
            "description": self.description,
        }


_P = frozenset({"P"})
ENZYMES: dict[str, EnzymeRule] = {
    "trypsin": EnzymeRule(
        "trypsin",
        "Trypsin",
        cleave_after=frozenset({"K", "R"}),
        cterm_block=_P,
        description="C-terminal to K/R, except when followed by P",
        source_note="classic trypsin cleavage rule",
    ),
    "arg_c": EnzymeRule(
        "arg_c",
        "Arg-C",
        cleave_after=frozenset({"R"}),
        cterm_block=_P,
        description="C-terminal to R, except when followed by P",
    ),
    "lys_c": EnzymeRule(
        "lys_c",
        "Lys-C",
        cleave_after=frozenset({"K"}),
        cterm_block=_P,
        description="C-terminal to K, except when followed by P",
    ),
    "chymotrypsin": EnzymeRule(
        "chymotrypsin",
        "alpha-Chymotrypsin (strict)",
        cleave_after=frozenset({"F", "Y", "W"}),
        cterm_block=_P,
        description="C-terminal to F/Y/W, except when followed by P",
    ),
    "glu_c": EnzymeRule(
        "glu_c",
        "Glu-C",
        cleave_after=frozenset({"E", "D"}),
        cterm_block=_P,
        description="C-terminal to E/D, except when followed by P",
    ),
    "asp_n": EnzymeRule(
        "asp_n",
        "Asp-N",
        cleave_before=frozenset({"D"}),
        description="N-terminal to D (no blocking context)",
    ),
    "pepsin": EnzymeRule(
        "pepsin",
        "Pepsin (pH 1.3 simplified)",
        cleave_before=frozenset({"F", "L", "W", "Y"}),
        nterm_block=_P,
        description="N-terminal to F/L/W/Y, except when preceded by P",
    ),
    "cnbr": EnzymeRule(
        "cnbr",
        "Cyanogen bromide",
        cleave_after=frozenset({"M"}),
        description="C-terminal to M (chemical cleavage, no blocking context)",
    ),
}


# ---------------------------------------------------------------------------
# Modification presets. Deltas are monoisotopic mass additions in Da.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Modification:
    key: str
    name: str
    delta: float
    targets: frozenset[str] = field(default_factory=frozenset)
    terminus: Literal["N", "C"] | None = None
    default_kind: Literal["fixed", "variable"] = "fixed"


MODIFICATIONS: dict[str, Modification] = {
    "carbamidomethyl_c": Modification(
        "carbamidomethyl_c", "Carbamidomethylation (C)", 57.021464, frozenset({"C"})
    ),
    "oxidation_m": Modification(
        "oxidation_m",
        "Oxidation (M)",
        15.994915,
        frozenset({"M"}),
        default_kind="variable",
    ),
    "phospho_sty": Modification(
        "phospho_sty",
        "Phosphorylation (S/T/Y)",
        79.966331,
        frozenset({"S", "T", "Y"}),
        default_kind="variable",
    ),
    "acetyl_nterm": Modification(
        "acetyl_nterm",
        "N-terminal acetylation",
        42.010565,
        terminus="N",
    ),
    "amide_cterm": Modification(
        "amide_cterm",
        "C-terminal amidation",
        -0.984016,
        terminus="C",
    ),
}
