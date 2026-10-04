"""Pydantic request/response schemas (kept separate from domain models)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CustomRule(BaseModel):
    """Ad-hoc cleavage rule supplied inline with a digest request."""

    cleave_after: str = Field("", description="residues after which the bond may cut")
    cleave_before: str = Field("", description="residues before which the bond may cut")
    cterm_block: str = Field("", description="for after-rules, residues on the C side that block")
    nterm_block: str = Field("", description="for before-rules, residues on the N side that block")
    name: str = Field("custom", description="label echoed back for traceability")


class ModificationSpec(BaseModel):
    key: str = Field(..., description="preset modification key")
    kind: Literal["fixed", "variable"] = Field("fixed")


class DigestRequest(BaseModel):
    sequence: str = Field(..., min_length=1, description="one-letter protein sequence")
    enzyme: str = Field("trypsin", description="enzyme key, or 'custom' with custom_rule")
    custom_rule: CustomRule | None = None
    missed_cleavages: int = Field(0, ge=0)
    fixed_modifications: list[ModificationSpec] = Field(default_factory=list)
    variable_modifications: list[ModificationSpec] = Field(default_factory=list)
    charges: list[int] = Field(
        default_factory=list, description="positive charge states for m/z reporting"
    )
    include_residues: bool = Field(
        False, description="include per-residue provenance annotations"
    )


class ValidationExpectation(BaseModel):
    """Independently authored expected outcome for one digest case."""

    case_name: str
    sequence: str
    enzyme: str = "trypsin"
    custom_rule: CustomRule | None = None
    missed_cleavages: int = 0
    fragment_count: int | None = None
    fragments: list[dict] | None = Field(
        None, description="expected {start,end,sequence,empty,missed_cleavages}"
    )
    cleavage_bonds: list[int] | None = None
    blocked_bonds: list[int] | None = None
    has_ambiguous: bool | None = None
    mass_uncertain: bool | None = None
    fragment_masses: dict[str, float] | None = Field(
        None, description="map of fragment sequence -> expected nominal neutral mass"
    )


class ValidationRequest(BaseModel):
    run_id: str | None = Field(
        None, description="existing run to validate; absent => run expectations directly"
    )
    expectations: list[ValidationExpectation]


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class MassView(BaseModel):
    nominal: float
    min_mass: float
    max_mass: float
    status: Literal["DETERMINATE", "UNCERTAIN", "UNKNOWN"]
    mz_by_charge: dict[int, float] = Field(default_factory=dict)


class ResidueView(BaseModel):
    position: int
    letter: str
    known: bool
    ambiguous: bool
    candidates: list[str]
    mass_min: float
    mass_max: float


class ModificationFormView(BaseModel):
    index: int
    delta: float
    nominal_mass: float
    placements: list[dict]
    nterm_mod: str | None
    cterm_mod: str | None


class FragmentView(BaseModel):
    index: int
    start: int
    end: int
    position: str = Field(..., description="1-based inclusive range label, e.g. 4-7")
    sequence: str
    empty: bool
    n_terminal: bool
    c_terminal: bool
    cleavage_before: int | None
    cleavage_after: int | None
    missed_cleavages: int
    internal_cleavage_bonds: list[int]
    mass: MassView
    modification_forms: list[ModificationFormView] = Field(default_factory=list)
    residues: list[ResidueView] = Field(default_factory=list)


class CleavageSiteView(BaseModel):
    bond: int
    rule: str
    rule_residue_position: int
    rule_residue: str
    blocked: bool
    blocking_residue_position: int | None = None
    blocking_residue: str | None = None


class DigestResponse(BaseModel):
    run_id: str
    status: Literal["OK"]
    service_version: str
    sequence: str
    sequence_length: int
    enzyme: dict
    missed_cleavages: int
    cleavage_sites: list[CleavageSiteView]
    blocked_sites: list[CleavageSiteView]
    fragment_count: int
    fragments: list[FragmentView]
    has_ambiguous: bool
    has_unknown: bool
    mass_uncertain: bool
    warnings: list[str]


class CaseResultView(BaseModel):
    case_name: str
    verdict: Literal["PASS", "FAIL"]
    run_id: str | None
    mismatches: list[str]
    fragment_count: int


class ValidationResponse(BaseModel):
    validation_id: str
    status: Literal["PASS", "FAIL"]
    service_version: str
    total: int
    passed: int
    failed: int
    cases: list[CaseResultView]


class RunSummaryView(BaseModel):
    run_id: str
    created_at: str
    enzyme_key: str
    sequence_length: int
    fragment_count: int
    status: str


class ErrorResponse(BaseModel):
    error: str
    message: str
    run_id: str | None = None
    position: int | None = None
    residue: str | None = None
    detail: dict | None = None
