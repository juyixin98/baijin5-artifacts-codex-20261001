"""Orchestration: resolve request inputs, run the domain engine, persist.

Routers stay thin; this module owns the parse -> digest -> view -> storage
pipeline and the translation between Pydantic views and domain objects.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app import __version__
from app.api.schemas import DigestRequest
from app.domain.constants import (
    ENZYMES,
    MODIFICATIONS,
    EnzymeRule,
    Modification,
)
from app.domain.errors import DigestError, ErrorCode
from app.domain.models import DigestResult, Fragment
from app.logging_config import StepTimer, bind_run, get_logger, reset_run
from app.services.digest import digest
from app.services.mass import mz
from app.services.parse import parse_sequence
from app.storage.store import DigestStore

_DETERMINATE_LETTERS = frozenset("ACDEFGHIKLMNPQRSTVWYU")


def _new_run_id() -> str:
    return "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]


def resolve_enzyme(request: DigestRequest) -> EnzymeRule:
    if request.enzyme == "custom":
        rule = request.custom_rule
        if rule is None or not (rule.cleave_after or rule.cleave_before):
            raise DigestError(
                ErrorCode.INVALID_CUSTOM_RULE,
                "custom enzyme requires at least one of cleave_after/cleave_before",
            )
        for field_name, value in (
            ("cleave_after", rule.cleave_after),
            ("cleave_before", rule.cleave_before),
            ("cterm_block", rule.cterm_block),
            ("nterm_block", rule.nterm_block),
        ):
            bad = sorted({c for c in value.upper() if c not in _DETERMINATE_LETTERS})
            if bad:
                raise DigestError(
                    ErrorCode.INVALID_CUSTOM_RULE,
                    f"{field_name} contains unsupported residues {bad}",
                    detail={"field": field_name, "unsupported": bad},
                )
        return EnzymeRule(
            key="custom",
            name=rule.name,
            cleave_after=frozenset(rule.cleave_after.upper()),
            cleave_before=frozenset(rule.cleave_before.upper()),
            cterm_block=frozenset(rule.cterm_block.upper()),
            nterm_block=frozenset(rule.nterm_block.upper()),
            description="request-supplied custom rule",
        )

    enzyme = ENZYMES.get(request.enzyme)
    if enzyme is None:
        raise DigestError(
            ErrorCode.INVALID_ENZYME,
            f"unknown enzyme {request.enzyme!r}",
            detail={"supported": sorted(ENZYMES)},
        )
    return enzyme


def resolve_modifications(
    specs, parsed, *, max_charge: int | None = None
) -> tuple[list[Modification], list[Modification]]:
    fixed: list[Modification] = []
    variable: list[Modification] = []
    for spec in specs:
        mod = MODIFICATIONS.get(spec.key)
        if mod is None:
            raise DigestError(
                ErrorCode.INVALID_VARIABLE_MOD,
                f"unknown modification {spec.key!r}",
                detail={"supported": sorted(MODIFICATIONS)},
            )
        (fixed if spec.kind == "fixed" else variable).append(mod)

    # A mod targeting residues that an ambiguous letter *could* denote cannot
    # be placed with certainty; refuse rather than silently under-modify.
    ambiguous_letters = {
        parsed.residues[pos - 1].letter for pos in parsed.ambiguous_positions
    }
    for mod in (*fixed, *variable):
        if mod.terminus is not None:
            continue
        for letter in ambiguous_letters:
            from app.domain.constants import AMBIGUOUS_RESIDUES

            candidates = set(AMBIGUOUS_RESIDUES[letter].candidates)
            if candidates & mod.targets:
                raise DigestError(
                    ErrorCode.MOD_TARGET_UNKNOWN_RESIDUE,
                    f"modification {mod.key!r} may apply to ambiguous residue "
                    f"{letter!r} (candidates {''.join(sorted(candidates))})",
                    detail={"modification": mod.key, "ambiguous_letter": letter},
                )
    return fixed, variable


def validate_charges(charges: list[int], max_charge: int) -> None:
    for charge in charges:
        if charge < 1 or charge > max_charge:
            raise DigestError(
                ErrorCode.INVALID_CHARGE,
                f"charge {charge} out of allowed range 1..{max_charge}",
            )
    if len(charges) != len(set(charges)):
        raise DigestError(ErrorCode.INVALID_CHARGE, "duplicate charge states")


def _fragment_view(
    fragment: Fragment,
    *,
    charges: list[int],
    decimals: int,
    include_residues: bool,
) -> dict[str, Any]:
    mz_by_charge = {
        charge: round(mz(fragment.mass.nominal, charge), decimals) for charge in charges
    }
    view: dict[str, Any] = {
        "index": fragment.index,
        "start": fragment.start,
        "end": fragment.end,
        "position": (
            f"{fragment.start + 1}-{fragment.end}"
            if not fragment.empty
            else f"{fragment.start}-{fragment.start} (empty)"
        ),
        "sequence": fragment.sequence,
        "empty": fragment.empty,
        "n_terminal": fragment.n_terminal,
        "c_terminal": fragment.c_terminal,
        "cleavage_before": fragment.cleavage_before,
        "cleavage_after": fragment.cleavage_after,
        "missed_cleavages": fragment.missed_cleavages,
        "internal_cleavage_bonds": list(fragment.internal_cleavage_bonds),
        "mass": {
            "nominal": round(fragment.mass.nominal, decimals),
            "min_mass": round(fragment.mass.min_mass, decimals),
            "max_mass": round(fragment.mass.max_mass, decimals),
            "status": fragment.mass.status.value,
            "mz_by_charge": mz_by_charge,
        },
        "modification_forms": [
            {
                "index": form.index,
                "delta": round(form.delta, decimals),
                "nominal_mass": round(fragment.mass.nominal + form.delta, decimals),
                "placements": [
                    {"position": pos, "residue": res, "modification": key}
                    for pos, res, key in form.placements
                ],
                "nterm_mod": form.nterm_mod,
                "cterm_mod": form.cterm_mod,
            }
            for form in fragment.modification_forms
        ],
    }
    if include_residues:
        view["residues"] = [
            {
                "position": res.position,
                "letter": res.letter,
                "known": res.known,
                "ambiguous": res.ambiguous,
                "candidates": list(res.candidates),
                "mass_min": round(res.mass_min, decimals),
                "mass_max": round(res.mass_max, decimals),
            }
            for res in fragment.residues
        ]
    return view


def _build_response(
    run_id: str, result: DigestResult, *, charges: list[int], decimals: int,
    include_residues: bool,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "OK",
        "service_version": __version__,
        "sequence": result.sequence,
        "sequence_length": result.sequence_length,
        "enzyme": result.enzyme.to_echo(),
        "missed_cleavages": result.missed_cleavages,
        "cleavage_sites": [site.to_trace() for site in result.cleavage_sites],
        "blocked_sites": [site.to_trace() for site in result.blocked_sites],
        "fragment_count": len(result.fragments),
        "fragments": [
            _fragment_view(
                fragment,
                charges=charges,
                decimals=decimals,
                include_residues=include_residues,
            )
            for fragment in result.fragments
        ],
        "has_ambiguous": result.has_ambiguous,
        "has_unknown": result.has_unknown,
        "mass_uncertain": result.mass_uncertain,
        "warnings": list(result.warnings),
    }


def _build_run_record(
    run_id: str, request: DigestRequest, result: DigestResult, decimals: int
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "sequence": result.sequence,
        "sequence_length": result.sequence_length,
        "enzyme_key": result.enzyme.key,
        "enzyme_echo": result.enzyme.to_echo(),
        "missed_cleavages": result.missed_cleavages,
        "fixed_mods": [s.model_dump() for s in request.fixed_modifications],
        "variable_mods": [s.model_dump() for s in request.variable_modifications],
        "fragment_count": len(result.fragments),
        "has_ambiguous": result.has_ambiguous,
        "has_unknown": result.has_unknown,
        "mass_uncertain": result.mass_uncertain,
        "warnings": list(result.warnings),
        "status": "OK",
        "service_version": __version__,
        "fragments": [
            {
                "fragment_index": f.index,
                "start": f.start,
                "end": f.end,
                "sequence": f.sequence,
                "empty": f.empty,
                "n_terminal": f.n_terminal,
                "c_terminal": f.c_terminal,
                "cleavage_before": f.cleavage_before,
                "cleavage_after": f.cleavage_after,
                "missed_cleavages": f.missed_cleavages,
                "internal_bonds": list(f.internal_cleavage_bonds),
                "mass_nominal": round(f.mass.nominal, decimals),
                "mass_min": round(f.mass.min_mass, decimals),
                "mass_max": round(f.mass.max_mass, decimals),
                "mass_status": f.mass.status.value,
                "modification_count": len(f.modification_forms),
                "payload": {"position_source": "parent_0_based_half_open"},
            }
            for f in result.fragments
        ],
    }


def run_digest(
    request: DigestRequest, *, settings, store: DigestStore | None = None
) -> dict[str, Any]:
    logger = get_logger()
    run_id = _new_run_id()
    # Correlate every downstream log line with this execution and its input.
    input_ref = f"enzyme={request.enzyme};len={len(request.sequence)};mc={request.missed_cleavages}"
    tokens = bind_run(run_id, input_ref)
    logger.info(
        "digest requested",
        extra={
            "extra_fields": {
                "event": "digest_request",
                "enzyme": request.enzyme,
                "sequence_length": len(request.sequence),
                "missed_cleavages": request.missed_cleavages,
            }
        },
    )

    try:
        if request.missed_cleavages < 0 or request.missed_cleavages > settings.max_missed_cleavages:
            raise DigestError(
                ErrorCode.INVALID_MISSED_CLEAVAGES,
                f"missed_cleavages must be in 0..{settings.max_missed_cleavages}",
            )
        validate_charges(request.charges, settings.max_charge)

        with StepTimer("parse_sequence", sequence_length=len(request.sequence)):
            parsed = parse_sequence(request.sequence, max_length=settings.max_sequence_length)

        enzyme = resolve_enzyme(request)

        with StepTimer("resolve_modifications"):
            fixed_mods, variable_mods = resolve_modifications(
                [*request.fixed_modifications, *request.variable_modifications], parsed
            )

        with StepTimer("digest", enzyme=enzyme.key, mc=request.missed_cleavages):
            result = digest(
                parsed,
                enzyme,
                missed_cleavages=request.missed_cleavages,
                fixed_mods=fixed_mods,
                variable_mods=variable_mods,
                max_modification_forms=settings.max_modification_forms,
            )

        response = _build_response(
            run_id,
            result,
            charges=request.charges,
            decimals=settings.mass_decimals,
            include_residues=request.include_residues,
        )
        if store is not None:
            with StepTimer("persist_run"):
                store.save_run(
                    _build_run_record(run_id, request, result, settings.mass_decimals)
                )

        logger.info(
            "digest completed",
            extra={
                "extra_fields": {
                    "event": "digest_complete",
                    "fragment_count": len(result.fragments),
                    "mass_uncertain": result.mass_uncertain,
                    "verdict": "OK",
                }
            },
        )
        return response
    finally:
        reset_run(tokens)
