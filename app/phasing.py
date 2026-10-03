"""Diploid haplotype phasing by exact Minimum Error Correction (MEC).

Model
-----
All sites are assumed heterozygous, so the two haplotypes of a block are
bit-complementary: choosing haplotype-1 bits ``h`` fixes haplotype 2 as
``1 - h``.  For a read ``r`` with informative calls on a block, the cost of
explaining it by haplotype ``h`` is the fixed quality-to-cost rule

    cost(r, h) = sum over called sites i of  qual(r, i) * [call(r, i) != h[i]]

i.e. a disagreement costs exactly the phred quality of the call, a match
costs 0, and unknown calls contribute nothing.  The MEC objective is

    MEC(h) = sum over reads r of min(cost(r, h), cost(r, 1 - h))

and is minimized by enumerating all ``2**(k-1)`` candidates with
``h[first_site] = 0`` — a whole-block phase flip (swapping the two
haplotypes) is the same biological solution, so pinning the first bit
removes that symmetry.  Blocks with more than ``max_enum_sites`` sites are
refused (ProcessingError BLOCK_TOO_LARGE) instead of being silently
approximated.

Blocks
------
Two sites are connected when at least one read has informative calls on
both.  Connected components of that graph are phased independently and
reported as separate blocks; no phase relation is ever claimed across
blocks.

Ambiguity
---------
Every candidate attaining the minimal MEC is kept.  More than one optimum
means the data do not determine a unique phase: the block is flagged
``ambiguous`` and all tied optima are reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

import numpy as np

from .errors import FailureCategory, ProcessingError
from .parsing import ALLELE_REF, NormalizedDataset

_CANDIDATE_CHUNK = 4096


# --------------------------------------------------------------------------
# Result structures
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Correction:
    site_id: str
    read_allele: str   # base observed in the read
    phased_allele: str  # base on the assigned haplotype
    qual: int


@dataclass(frozen=True)
class ReadAssignment:
    read_id: str
    assigned_haplotype: int | None  # 1, 2, or None when both explain equally well
    correction_cost: int
    corrections: tuple[Correction, ...]


@dataclass
class PhaseBlock:
    block_index: int
    site_ids: list[str]
    haplotype1: list[str]  # bases, canonical orientation (first site = ref bit 0)
    haplotype2: list[str]
    mec: int
    n_covering_reads: int
    ambiguous: bool
    n_optima: int
    alternative_optima: list[dict] = field(default_factory=list)
    is_singleton: bool = False
    supporting_reads: int = 0
    conflicting_reads: int = 0
    read_assignments: list[ReadAssignment] = field(default_factory=list)


@dataclass
class PhaseResult:
    sample_id: str
    blocks: list[PhaseBlock]
    total_mec: int
    warnings: list[str]
    uncertainties: list[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def canonical_orientation(bits: tuple[int, ...]) -> tuple[int, ...]:
    """Map a haplotype bit vector to its flip-equivalence representative.

    A whole-haplotype flip (swapping the two haplotypes of a block) is the
    same solution, so the representative is the orientation whose first bit
    is 0.
    """
    if not bits:
        return bits
    if bits[0] == 0:
        return tuple(int(b) for b in bits)
    return tuple(1 - int(b) for b in bits)


def find_blocks(observed: np.ndarray) -> list[list[int]]:
    """Connected components of the site co-coverage graph.

    ``observed`` is the (n_reads, n_sites) boolean matrix of informative
    calls.  Returns site-index lists in input order, blocks ordered by their
    smallest site index.
    """
    n_sites = observed.shape[1]
    if n_sites == 0:
        return []
    co_covered = (observed.T.astype(np.int64) @ observed.astype(np.int64)) > 0
    np.fill_diagonal(co_covered, False)

    blocks: list[list[int]] = []
    unseen = set(range(n_sites))
    while unseen:
        seed = min(unseen)
        component = []
        stack = [seed]
        unseen.discard(seed)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbour in np.flatnonzero(co_covered[node]):
                if int(neighbour) in unseen:
                    unseen.discard(int(neighbour))
                    stack.append(int(neighbour))
        blocks.append(sorted(component))
    blocks.sort(key=min)
    return blocks


def _candidate_haplotypes(k: int) -> np.ndarray:
    """All {0,1}^k vectors with first bit 0, shape (2**(k-1), k)."""
    if k == 1:
        return np.zeros((1, 1), dtype=np.int8)
    rest = np.array(list(product((0, 1), repeat=k - 1)), dtype=np.int8)
    return np.concatenate([np.zeros((len(rest), 1), dtype=np.int8), rest], axis=1)


def _mec_scores(alleles: np.ndarray, quals: np.ndarray, observed: np.ndarray,
                  candidates: np.ndarray) -> np.ndarray:
    """MEC score of every candidate haplotype (vectorized in chunks)."""
    qual_sum = (quals * observed).sum(axis=1).astype(np.int64)  # (n_reads,)
    scores = np.empty(len(candidates), dtype=np.int64)
    for start in range(0, len(candidates), _CANDIDATE_CHUNK):
        chunk = candidates[start:start + _CANDIDATE_CHUNK]          # (m, k)
        mismatch = observed[:, None, :] & (alleles[:, None, :] != chunk[None, :, :])
        cost1 = (quals[:, None, :] * mismatch).sum(axis=2)          # (n, m)
        cost2 = qual_sum[:, None] - cost1  # complementary haplotype mismatches
        scores[start:start + len(chunk)] = np.minimum(cost1, cost2).sum(axis=0)
    return scores


# --------------------------------------------------------------------------
# Block phasing
# --------------------------------------------------------------------------

def _bits_to_bases(bits, site_cols, ref_bases, alt_bases) -> list[str]:
    return [
        ref_bases[col] if bit == ALLELE_REF else alt_bases[col]
        for bit, col in zip(bits, site_cols)
    ]


def _assign_reads(ds: NormalizedDataset, site_cols: list[int],
                  h1: np.ndarray) -> tuple[list[ReadAssignment], int, int]:
    """Assign each covering read to the cheaper haplotype; collect evidence."""
    sub_alleles = ds.alleles[:, site_cols]
    sub_quals = ds.quals[:, site_cols]
    sub_obs = ds.observed[:, site_cols]
    covering = np.flatnonzero(sub_obs.any(axis=1))

    assignments: list[ReadAssignment] = []
    n_support = 0
    n_conflict = 0
    h2 = 1 - h1
    for r in covering:
        mism1 = sub_obs[r] & (sub_alleles[r] != h1)
        cost1 = int((sub_quals[r] * mism1).sum())
        cost2 = int((sub_quals[r] * (sub_obs[r] & (sub_alleles[r] != h2))).sum())
        if cost1 < cost2:
            assigned, hap_bits, mism = 1, h1, mism1
        elif cost2 < cost1:
            assigned, hap_bits, mism = 2, h2, sub_obs[r] & (sub_alleles[r] != h2)
        else:
            assigned, hap_bits, mism = None, h1, mism1
        corrections = tuple(
            Correction(
                site_id=ds.site_ids[site_cols[j]],
                read_allele=(ds.ref_bases[site_cols[j]] if sub_alleles[r, j] == ALLELE_REF
                             else ds.alt_bases[site_cols[j]]),
                phased_allele=(ds.ref_bases[site_cols[j]] if hap_bits[j] == ALLELE_REF
                               else ds.alt_bases[site_cols[j]]),
                qual=int(sub_quals[r, j]),
            )
            for j in np.flatnonzero(mism)
        )
        cost = min(cost1, cost2)
        if cost == 0:
            n_support += 1
        else:
            n_conflict += 1
        assignments.append(ReadAssignment(
            read_id=ds.read_ids[int(r)],
            assigned_haplotype=assigned,
            correction_cost=cost,
            corrections=corrections,
        ))
    return assignments, n_support, n_conflict


def _phase_block(ds: NormalizedDataset, block_index: int, site_cols: list[int],
                 max_enum_sites: int) -> tuple[PhaseBlock, list[str]]:
    uncertainties: list[str] = []
    k = len(site_cols)
    if k > max_enum_sites:
        raise ProcessingError(
            FailureCategory.BLOCK_TOO_LARGE,
            f"block {block_index} has {k} connected sites, above the exact "
            f"enumeration limit of {max_enum_sites}",
            {"block_index": block_index, "n_sites": k, "max_enum_sites": max_enum_sites},
        )

    sub_alleles = ds.alleles[:, site_cols]
    sub_quals = ds.quals[:, site_cols]
    sub_obs = ds.observed[:, site_cols]

    candidates = _candidate_haplotypes(k)
    scores = _mec_scores(sub_alleles, sub_quals, sub_obs, candidates)
    best = int(scores.min())
    optima = np.flatnonzero(scores == best)

    h1 = candidates[int(optima[0])]
    h2 = 1 - h1
    ambiguous = len(optima) > 1
    alternatives = [
        {
            "haplotype1": _bits_to_bases(candidates[int(i)], site_cols, ds.ref_bases, ds.alt_bases),
            "haplotype2": _bits_to_bases(1 - candidates[int(i)], site_cols, ds.ref_bases, ds.alt_bases),
        }
        for i in optima[1:]
    ]

    assignments, n_support, n_conflict = _assign_reads(ds, site_cols, h1)
    if not assignments:
        uncertainties.append(
            f"block {block_index} (sites {', '.join(ds.site_ids[c] for c in site_cols)}) "
            "has no covering read; its phase is unsupported"
        )
    if ambiguous:
        uncertainties.append(
            f"block {block_index} has {len(optima)} MEC-optimal phases (MEC={best}); "
            "the data do not determine a unique phase"
        )
    is_singleton = k == 1
    if is_singleton:
        uncertainties.append(
            f"site {ds.site_ids[site_cols[0]]} is not co-covered with any other site; "
            "it cannot be phased relative to them"
        )

    block = PhaseBlock(
        block_index=block_index,
        site_ids=[ds.site_ids[c] for c in site_cols],
        haplotype1=_bits_to_bases(h1, site_cols, ds.ref_bases, ds.alt_bases),
        haplotype2=_bits_to_bases(h2, site_cols, ds.ref_bases, ds.alt_bases),
        mec=best,
        n_covering_reads=len(assignments),
        ambiguous=ambiguous,
        n_optima=len(optima),
        alternative_optima=alternatives,
        is_singleton=is_singleton,
        supporting_reads=n_support,
        conflicting_reads=n_conflict,
        read_assignments=assignments,
    )
    return block, uncertainties


def phase(ds: NormalizedDataset, max_enum_sites: int) -> PhaseResult:
    """Phase every connected block of the dataset independently."""
    if not ds.observed.any():
        raise ProcessingError(
            FailureCategory.NO_INFORMATIVE_READS,
            "no read carries an informative (non-unknown) allele call",
        )

    warnings: list[str] = []
    if ds.n_unknown_calls:
        warnings.append(
            f"{ds.n_unknown_calls} read call(s) have unknown allele and were "
            "excluded from the MEC objective"
        )
    single_site_reads = sum(
        1 for r in range(ds.n_reads) if ds.observed[r].sum() < 2
    )
    if single_site_reads:
        warnings.append(
            f"{single_site_reads} read(s) cover fewer than 2 sites and do not "
            "connect any site pair"
        )

    blocks: list[PhaseBlock] = []
    uncertainties: list[str] = []
    for block_index, site_cols in enumerate(find_blocks(ds.observed)):
        block, block_uncertainties = _phase_block(ds, block_index, site_cols, max_enum_sites)
        blocks.append(block)
        uncertainties.extend(block_uncertainties)

    return PhaseResult(
        sample_id=ds.sample_id,
        blocks=blocks,
        total_mec=sum(b.mec for b in blocks),
        warnings=warnings,
        uncertainties=uncertainties,
    )


# --------------------------------------------------------------------------
# Serialization (API responses and provenance records share this shape)
# --------------------------------------------------------------------------

def result_to_dict(result: PhaseResult) -> dict:
    return {
        "sample_id": result.sample_id,
        "total_mec": result.total_mec,
        "n_blocks": len(result.blocks),
        "blocks": [
            {
                "block_index": b.block_index,
                "site_ids": b.site_ids,
                "haplotype1": b.haplotype1,
                "haplotype2": b.haplotype2,
                "mec": b.mec,
                "n_covering_reads": b.n_covering_reads,
                "supporting_reads": b.supporting_reads,
                "conflicting_reads": b.conflicting_reads,
                "ambiguous": b.ambiguous,
                "n_optima": b.n_optima,
                "alternative_optima": b.alternative_optima,
                "is_singleton": b.is_singleton,
                "read_assignments": [
                    {
                        "read_id": a.read_id,
                        "assigned_haplotype": a.assigned_haplotype,
                        "correction_cost": a.correction_cost,
                        "corrections": [
                            {
                                "site_id": c.site_id,
                                "read_allele": c.read_allele,
                                "phased_allele": c.phased_allele,
                                "qual": c.qual,
                            }
                            for c in a.corrections
                        ],
                    }
                    for a in b.read_assignments
                ],
            }
            for b in result.blocks
        ],
        "warnings": result.warnings,
        "uncertainties": result.uncertainties,
    }
