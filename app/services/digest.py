"""Rule-based cleavage and fragment enumeration.

Algorithm
---------
1. Walk every internal bond ``1 .. n-1`` (bonds 0 and n are the protein
   boundaries and never cut). A bond is a *rule match* when the residue on the
   rule side belongs to ``cleave_after`` / ``cleave_before``; a matched bond
   whose other side residue is in the matching blocking set is recorded as
   ``blocked`` and does not cut.
2. ``cut_bonds`` = matched, unblocked bonds, sorted ascending.
3. Every contiguous span of up to ``missed_cleavages + 1`` fully-digested
   segments is enumerated exactly once. Spans that begin/end at a protein
   boundary carry the N/C-terminal flags. When a boundary bond itself is a
   rule match (Asp-N on an N-terminal D; C-terminal rule residue), the
   adjacent terminal span is empty and is emitted explicitly rather than
   dropped - with zero-based, half-open coordinates ``start == end``.
4. Fragments keep absolute parent positions; identical peptide strings at
   different positions are never merged.

This module performs no I/O and raises no HTTP types.
"""

from __future__ import annotations

from collections.abc import Sequence as ABCSequence

from app.domain.constants import WATER_MASS, EnzymeRule, Modification
from app.domain.errors import DigestError, ErrorCode
from app.domain.models import (
    CleavageSite,
    DigestResult,
    Fragment,
    MassStatus,
    ModificationForm,
    ResidueAnnotation,
)

# Bonds eligible for actual cleavage plus every blocked rule match are
# evaluated; boundary bonds are inspected only for empty-segment bookkeeping.


def _boundary_match(
    enzyme: EnzymeRule, sequence: str, bond: int
) -> tuple[bool, str]:
    """Return (matches, reason) for bond 0 (N boundary) or bond n (C boundary)."""
    n = len(sequence)
    if bond == 0:
        # before-rule on the first residue; no residue exists on the N side.
        if sequence and sequence[0] in enzyme.cleave_before:
            return True, f"before:{sequence[0]}"
        return False, ""
    if bond == n:
        if sequence and sequence[-1] in enzyme.cleave_after:
            return True, f"after:{sequence[-1]}"
        return False, ""
    return False, ""


def evaluate_sites(enzyme: EnzymeRule, sequence: str) -> tuple[list[CleavageSite], list[CleavageSite]]:
    """Evaluate all bonds. Returns (eligible_sites, blocked_sites).

    Boundary bonds can match a rule (relevant for empty segments) but are
    returned in neither list: they cannot be cut to extend the protein.
    """
    n = len(sequence)
    eligible: list[CleavageSite] = []
    blocked: list[CleavageSite] = []

    for bond in range(1, n):
        left = sequence[bond - 1]
        right = sequence[bond]
        matched = False
        reason = ""
        rule_pos = bond  # 1-based position of rule residue

        if left in enzyme.cleave_after:
            matched = True
            reason = f"after:{left}"
            rule_pos = bond
        elif right in enzyme.cleave_before:
            matched = True
            reason = f"before:{right}"
            rule_pos = bond + 1

        if not matched:
            continue

        is_blocked = False
        blocking_pos: int | None = None
        blocking_res: str | None = None

        if reason.startswith("after:") and right in enzyme.cterm_block:
            is_blocked = True
            blocking_pos = bond + 1
            blocking_res = right
        elif reason.startswith("before:") and left in enzyme.nterm_block:
            is_blocked = True
            blocking_pos = bond
            blocking_res = left

        site = CleavageSite(
            bond=bond,
            enzyme=enzyme.key,
            reason=reason,
            terminal_residue_position=rule_pos,
            terminal_residue=reason.split(":", 1)[1],
            blocked=is_blocked,
            blocking_residue_position=blocking_pos,
            blocking_residue=blocking_res,
        )
        (blocked if is_blocked else eligible).append(site)

    return eligible, blocked


def boundary_rule_bonds(enzyme: EnzymeRule, sequence: str) -> set[int]:
    """Bonds 0/n that match a rule - explicit empty segment anchors."""
    anchors: set[int] = set()
    n = len(sequence)
    if n == 0:
        return anchors
    if _boundary_match(enzyme, sequence, 0)[0]:
        anchors.add(0)
    if _boundary_match(enzyme, sequence, n)[0]:
        anchors.add(n)
    return anchors


def enumerate_modification_forms(
    residues: ABCSequence[ResidueAnnotation],
    *,
    n_terminal: bool,
    c_terminal: bool,
    fixed_mods: list[Modification],
    variable_mods: list[Modification],
    max_forms: int,
) -> tuple[ModificationForm, ...]:
    """Enumerate modification realizations of one fragment.

    Fixed modifications apply to every target residue unconditionally.
    Variable modifications are independently applicable at each target
    occurrence (subset enumeration). Terminal mods apply only when the
    fragment actually carries the corresponding protein terminus.
    """
    placements_fixed: list[tuple[int, str, str]] = []
    fixed_delta = 0.0
    nterm_fixed: str | None = None
    cterm_fixed: str | None = None

    for mod in fixed_mods:
        if mod.terminus == "N":
            if n_terminal:
                fixed_delta += mod.delta
                nterm_fixed = mod.key
            continue
        if mod.terminus == "C":
            if c_terminal:
                fixed_delta += mod.delta
                cterm_fixed = mod.key
            continue
        for res in residues:
            if res.letter in mod.targets and not res.ambiguous:
                placements_fixed.append((res.position, res.letter, mod.key))
                fixed_delta += mod.delta

    # Variable candidate sites: (position, letter, mod_key, delta)
    candidates: list[tuple[int, str, str, float]] = []
    nterm_variable: list[Modification] = []
    cterm_variable: list[Modification] = []
    for mod in variable_mods:
        if mod.terminus == "N":
            if n_terminal:
                nterm_variable.append(mod)
            continue
        if mod.terminus == "C":
            if c_terminal:
                cterm_variable.append(mod)
            continue
        for res in residues:
            if res.letter in mod.targets and not res.ambiguous:
                candidates.append((res.position, res.letter, mod.key, mod.delta))

    # Terminal variable modifications are independent binary choices.
    terminal_options: list[tuple[str, float]] = []
    for mod in nterm_variable:
        terminal_options.append((f"N:{mod.key}", mod.delta))
    for mod in cterm_variable:
        terminal_options.append((f"C:{mod.key}", mod.delta))

    total_forms = (2 ** len(candidates)) * (2 ** len(terminal_options))
    if total_forms > max_forms:
        raise DigestError(
            ErrorCode.MOD_FORM_LIMIT,
            f"modification form count {total_forms} exceeds limit {max_forms}",
            detail={"form_count": total_forms, "limit": max_forms},
        )

    forms: list[ModificationForm] = []
    form_index = 0

    # Fixed placements always present.
    fixed_tuple = tuple(placements_fixed)

    for site_mask in range(2 ** len(candidates)):
        chosen: list[tuple[int, str, str]] = []
        delta = fixed_delta
        for bit, (pos, letter, key, mod_delta) in enumerate(candidates):
            if site_mask & (1 << bit):
                chosen.append((pos, letter, key))
                delta += mod_delta
        for term_mask in range(2 ** len(terminal_options)):
            term_chosen = list(chosen)
            term_delta = delta
            nterm_mod = nterm_fixed
            cterm_mod = cterm_fixed
            for bit, (label, mod_delta) in enumerate(terminal_options):
                if term_mask & (1 << bit):
                    side, key = label.split(":", 1)
                    term_delta += mod_delta
                    if side == "N":
                        nterm_mod = key
                    else:
                        cterm_mod = key
            forms.append(
                ModificationForm(
                    index=form_index,
                    delta=term_delta,
                    placements=fixed_tuple + tuple(term_chosen),
                    nterm_mod=nterm_mod,
                    cterm_mod=cterm_mod,
                )
            )
            form_index += 1

    return tuple(forms)


def digest(
    parsed_sequence,
    enzyme: EnzymeRule,
    *,
    missed_cleavages: int,
    fixed_mods: list[Modification] | None = None,
    variable_mods: list[Modification] | None = None,
    max_modification_forms: int = 1024,
) -> DigestResult:
    """Enumerate fragments with up to ``missed_cleavages`` missed sites.

    Physical (non-empty) fragments are contiguous spans of 1..mc+1 fully
    digested segments. Boundary empty segments - produced only when a rule
    matches bond 0 or bond n - are emitted as standalone fragments and never
    participate in missed-cleavage merges (there is nothing to merge).
    """
    fixed_mods = fixed_mods or []
    variable_mods = variable_mods or []
    sequence = parsed_sequence.sequence
    n = len(sequence)

    eligible, blocked = evaluate_sites(enzyme, sequence)
    cut_bonds = sorted(site.bond for site in eligible)
    anchors = boundary_rule_bonds(enzyme, sequence)

    # Physical fully-digested segment edges: 0, cut bonds, n (deduplicated).
    edges = [0]
    for bond in cut_bonds:
        edges.append(bond)
    if edges[-1] != n:
        edges.append(n)
    segments = [(edges[k], edges[k + 1]) for k in range(len(edges) - 1)]

    fragments: list[Fragment] = []
    frag_index = 0

    def add_fragment(start: int, end: int) -> None:
        nonlocal frag_index
        residues = tuple(parsed_sequence.residues[start:end])
        empty = start == end
        n_term = start == 0
        c_term = end == n
        internal = tuple(b for b in cut_bonds if start < b < end)
        forms = ()
        if not empty:
            forms = enumerate_modification_forms(
                residues,
                n_terminal=n_term,
                c_terminal=c_term,
                fixed_mods=fixed_mods,
                variable_mods=variable_mods,
                max_forms=max_modification_forms,
            )
        fragments.append(
            Fragment(
                index=frag_index,
                start=start,
                end=end,
                sequence=sequence[start:end],
                n_terminal=n_term,
                c_terminal=c_term,
                empty=empty,
                cleavage_before=None if n_term else start,
                cleavage_after=None if c_term else end,
                missed_cleavages=len(internal),
                internal_cleavage_bonds=internal,
                mass=mass_from_annotations(residues, empty),
                residues=residues,
                modification_forms=forms,
            )
        )
        frag_index += 1

    # Standalone N-terminal empty segment when a before-rule matches bond 0.
    if 0 in anchors:
        add_fragment(0, 0)

    # Physical fragments: every span of up to mc+1 consecutive segments.
    span_size = missed_cleavages + 1
    for i in range(len(segments)):
        for j in range(i, min(i + span_size, len(segments))):
            add_fragment(segments[i][0], segments[j][1])

    # Standalone C-terminal empty segment when an after-rule matches bond n.
    if n in anchors:
        add_fragment(n, n)

    mass_uncertain = any(f.mass.status == MassStatus.UNCERTAIN for f in fragments)
    warnings: list[str] = []
    if blocked:
        warnings.append(
            f"{len(blocked)} rule match(es) suppressed by blocking context"
        )
    if anchors:
        warnings.append(
            "cleavage rule matches a protein terminus; explicit empty segment emitted"
        )

    return DigestResult(
        run_id="",
        sequence=sequence,
        sequence_length=n,
        enzyme=enzyme,
        missed_cleavages=missed_cleavages,
        cleavage_sites=tuple(eligible),
        blocked_sites=tuple(blocked),
        fragments=tuple(fragments),
        has_ambiguous=parsed_sequence.has_ambiguous,
        has_unknown=parsed_sequence.has_unknown,
        mass_uncertain=mass_uncertain,
        warnings=tuple(warnings),
    )


def mass_from_annotations(
    residues: ABCSequence[ResidueAnnotation], empty: bool
) -> "MassInterval":
    """Neutral mass of a span. Empty segments weigh nothing (no H2O added)."""
    from app.domain.models import MassInterval

    low = 0.0 if empty else WATER_MASS
    high = 0.0 if empty else WATER_MASS
    determinate = True
    for residue in residues:
        low += residue.mass_min
        high += residue.mass_max
        if residue.mass_min != residue.mass_max:
            determinate = False
    nominal = low if determinate else (low + high) / 2.0
    status = MassStatus.DETERMINATE if determinate else MassStatus.UNCERTAIN
    return MassInterval(nominal=nominal, min_mass=low, max_mass=high, status=status)
