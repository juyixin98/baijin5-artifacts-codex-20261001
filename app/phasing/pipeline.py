"""Phasing pipeline: parse -> blocks -> per-block exact MEC -> result assembly.

Produces the full result dict returned by the API and stored in provenance.
Failures raise PhasingError; uncertain-but-valid conclusions (ambiguity,
ties, unknown alleles, uncovered variants) are collected in
``uncertainties`` instead of failing the run.
"""

from __future__ import annotations

import numpy as np

from app.config import Settings
from app.domain import ParsedInput, Variant
from app.errors import FailureCategory, PhasingError
from app.phasing.blocks import find_blocks
from app.phasing.matrix import build_matrix
from app.phasing.mec import MECResult, solve_mec
from app.version import ALGORITHM_VERSION, APP_VERSION

PHASE_NOTE = "phase is defined within this block only; no phase relation to other blocks is inferred"


def _allele_strings(variant: Variant, bits: np.ndarray, site_pos: int) -> str:
    return variant.ref if int(bits[site_pos]) == 0 else variant.alt


def _block_evidence(
    parsed: ParsedInput,
    block_sites: list[int],
    matrix,
    mec: MECResult,
) -> dict:
    """Support and conflict evidence for one block under its primary optimum."""
    primary = mec.optimal_haplotypes[0]
    per_site = []
    for col, site in enumerate(block_sites):
        variant = parsed.variants[site]
        covered = matrix.covered[:, col]
        ref_weight = float((matrix.weights[:, col] * covered * (matrix.alleles[:, col] == 0)).sum())
        alt_weight = float((matrix.weights[:, col] * covered * (matrix.alleles[:, col] == 1)).sum())
        # Conflict: observations contradicting the haplotype their fragment
        # was assigned to (these are exactly the MEC corrections at this site).
        assigned_bit = np.where(mec.assignments == 0, primary[col], 1 - primary[col])
        mismatched = covered & (matrix.alleles[:, col] != assigned_bit)
        correction_weight = float((matrix.weights[:, col] * mismatched).sum())
        per_site.append({
            "variant_id": variant.id,
            "ref_support_weight": ref_weight,
            "alt_support_weight": alt_weight,
            "correction_weight": correction_weight,
            "correction_count": int(mismatched.sum()),
        })
    return {
        "num_fragments": matrix.num_fragments,
        "corrected_observations": int(sum(s["correction_count"] for s in per_site)),
        "tied_assignments": len(mec.tied_fragments),
        "per_site": per_site,
    }


def _phase_block(parsed: ParsedInput, block_sites: list[int], settings: Settings) -> dict:
    if len(block_sites) > settings.max_enum_sites:
        raise PhasingError(
            FailureCategory.BLOCK_TOO_LARGE,
            f"block starting at variant {parsed.variants[block_sites[0]].id!r} has "
            f"{len(block_sites)} sites, exceeding exact-enumeration limit "
            f"{settings.max_enum_sites}",
        )
    matrix = build_matrix(parsed.fragments, block_sites)
    mec = solve_mec(matrix)

    variants = [parsed.variants[s] for s in block_sites]
    primary = mec.optimal_haplotypes[0]
    hap1 = [_allele_strings(v, primary, i) for i, v in enumerate(variants)]
    hap2 = [_allele_strings(v, 1 - primary, i) for i, v in enumerate(variants)]

    alternatives = []
    for hap in mec.optimal_haplotypes[1 : settings.max_reported_solutions]:
        alternatives.append({
            "haplotype_1": [_allele_strings(v, hap, i) for i, v in enumerate(variants)],
            "haplotype_2": [_allele_strings(v, 1 - hap, i) for i, v in enumerate(variants)],
            "mec_score": mec.best_score,
        })

    return {
        "variant_ids": [v.id for v in variants],
        "num_sites": len(block_sites),
        "haplotypes": {"H1": hap1, "H2": hap2},
        "mec_score": mec.best_score,
        "num_optimal_solutions": len(mec.optimal_haplotypes),
        "ambiguous": len(mec.optimal_haplotypes) > 1,
        "alternative_solutions": alternatives,
        "alternatives_truncated": len(mec.optimal_haplotypes) - 1 > len(alternatives),
        "evidence": _block_evidence(parsed, block_sites, matrix, mec),
        "phase_note": PHASE_NOTE,
    }


def run_phasing(parsed: ParsedInput, settings: Settings) -> dict:
    """Run the full pipeline over parsed input; returns the result payload."""
    uncertainties: list[str] = []

    if parsed.unknown_allele_observations:
        uncertainties.append(
            f"{parsed.unknown_allele_observations} observation(s) carried an allele matching "
            "neither ref nor alt and were excluded from the MEC objective"
        )
    if parsed.defaulted_quality_observations:
        uncertainties.append(
            f"{parsed.defaulted_quality_observations} observation(s) had no quality; "
            f"default cost {settings.default_quality} was applied"
        )
    if not parsed.fragments:
        raise PhasingError(
            FailureCategory.NO_OBSERVATIONS,
            "no usable read observations (all missing or all unknown alleles)",
        )

    blocks = find_blocks(len(parsed.variants), parsed.fragments)
    covered_sites = {site for f in parsed.fragments for site in f.sites}
    for variant in parsed.variants:
        if variant.index not in covered_sites:
            uncertainties.append(f"variant {variant.id!r} has no covering reads")

    block_results = []
    for block_sites in blocks:
        result = _phase_block(parsed, block_sites, settings)
        if result["ambiguous"]:
            uncertainties.append(
                f"block {result['variant_ids']}: {result['num_optimal_solutions']} "
                "haplotype pairs achieve the minimum MEC score; the phase is not "
                "uniquely determined"
            )
        if result["evidence"]["tied_assignments"]:
            uncertainties.append(
                f"block {result['variant_ids']}: {result['evidence']['tied_assignments']} "
                "fragment(s) are equally close to both haplotypes"
            )
        block_results.append(result)

    ambiguous = any(b["ambiguous"] for b in block_results)
    return {
        "status": "AMBIGUOUS" if ambiguous else "OK",
        "failure": None,
        "uncertainties": uncertainties,
        "versions": {
            "app": APP_VERSION,
            "algorithm": ALGORITHM_VERSION,
            "config": {
                "max_enum_sites": settings.max_enum_sites,
                "default_quality": settings.default_quality,
                "max_quality": settings.max_quality,
            },
        },
        "summary": {
            "num_variants": len(parsed.variants),
            "num_fragments": len(parsed.fragments),
            "num_blocks": len(block_results),
            "unknown_allele_observations": parsed.unknown_allele_observations,
            "total_mec_score": float(sum(b["mec_score"] for b in block_results)),
        },
        "blocks": block_results,
    }
