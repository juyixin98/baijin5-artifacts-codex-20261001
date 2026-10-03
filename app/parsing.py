"""Dataset validation and normalization.

Turns a PhaseRequest into dense NumPy arrays that the phasing core works
on, and rejects malformed input with categorized DatasetError failures:

- reference allele must be a concrete A/C/G/T base (an unknown or
  degenerate reference allele, e.g. "N", is INVALID_REFERENCE_ALLELE);
- alt allele likewise, and must differ from ref;
- site/read ids unique; a read may not call the same site twice;
- read calls must reference declared sites;
- qualities must be within [0, max_quality].

Calls with allele "unknown" are kept in the dataset (encoded as -1) but
contribute nothing to the MEC objective; they are surfaced as warnings.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import DatasetError, FailureCategory
from .models import PhaseRequest

VALID_BASES = frozenset("ACGT")

# Allele encoding used across the phasing core.
ALLELE_REF = 0
ALLELE_ALT = 1
ALLELE_UNKNOWN = -1


@dataclass(frozen=True)
class NormalizedDataset:
    sample_id: str
    site_ids: list[str]
    site_chroms: list[str]
    site_positions: list[int]
    ref_bases: list[str]
    alt_bases: list[str]
    read_ids: list[str]
    # (n_reads, n_sites) int8: ALLELE_REF / ALLELE_ALT / ALLELE_UNKNOWN.
    alleles: np.ndarray
    # (n_reads, n_sites) int16: phred quality where a known call exists, 0 elsewhere.
    quals: np.ndarray
    # (n_reads, n_sites) bool: True where the read has an informative call.
    observed: np.ndarray
    n_unknown_calls: int

    @property
    def n_sites(self) -> int:
        return len(self.site_ids)

    @property
    def n_reads(self) -> int:
        return len(self.read_ids)


def _validate_sites(request: PhaseRequest) -> None:
    seen: set[str] = set()
    for site in request.sites:
        if site.id in seen:
            raise DatasetError(
                FailureCategory.DUPLICATE_SITE_ID,
                f"duplicate site id {site.id!r}",
                {"site_id": site.id},
            )
        seen.add(site.id)
        ref = site.ref.upper()
        alt = site.alt.upper()
        if ref not in VALID_BASES:
            raise DatasetError(
                FailureCategory.INVALID_REFERENCE_ALLELE,
                f"site {site.id!r} has unknown/invalid reference allele {site.ref!r}; "
                "expected one of A/C/G/T",
                {"site_id": site.id, "ref": site.ref},
            )
        if alt not in VALID_BASES or alt == ref:
            raise DatasetError(
                FailureCategory.INVALID_ALT_ALLELE,
                f"site {site.id!r} has invalid alt allele {site.alt!r}; "
                "expected one of A/C/G/T different from ref",
                {"site_id": site.id, "ref": site.ref, "alt": site.alt},
            )


def _validate_reads(request: PhaseRequest, site_ids: set[str], max_quality: int) -> None:
    seen_reads: set[str] = set()
    for read in request.reads:
        if read.id in seen_reads:
            raise DatasetError(
                FailureCategory.DUPLICATE_READ_ID,
                f"duplicate read id {read.id!r}",
                {"read_id": read.id},
            )
        seen_reads.add(read.id)
        called: set[str] = set()
        for call in read.calls:
            if call.site not in site_ids:
                raise DatasetError(
                    FailureCategory.READ_REFERENCES_UNKNOWN_SITE,
                    f"read {read.id!r} calls undeclared site {call.site!r}",
                    {"read_id": read.id, "site_id": call.site},
                )
            if call.site in called:
                raise DatasetError(
                    FailureCategory.DUPLICATE_CALL_IN_READ,
                    f"read {read.id!r} calls site {call.site!r} more than once",
                    {"read_id": read.id, "site_id": call.site},
                )
            called.add(call.site)
            if call.allele != "unknown" and not (0 <= call.qual <= max_quality):
                raise DatasetError(
                    FailureCategory.QUALITY_OUT_OF_RANGE,
                    f"read {read.id!r} site {call.site!r}: qual {call.qual} outside "
                    f"[0, {max_quality}]",
                    {"read_id": read.id, "site_id": call.site, "qual": call.qual},
                )


def normalize(request: PhaseRequest, max_quality: int) -> NormalizedDataset:
    """Validate a PhaseRequest and build dense arrays for the phasing core."""
    if not request.sites or not request.reads:
        raise DatasetError(
            FailureCategory.EMPTY_DATASET,
            "dataset must declare at least one site and one read",
        )
    _validate_sites(request)
    site_ids = [s.id for s in request.sites]
    _validate_reads(request, set(site_ids), max_quality)

    site_index = {sid: i for i, sid in enumerate(site_ids)}
    n_sites = len(site_ids)
    n_reads = len(request.reads)
    alleles = np.full((n_reads, n_sites), ALLELE_UNKNOWN, dtype=np.int8)
    quals = np.zeros((n_reads, n_sites), dtype=np.int16)
    n_unknown = 0
    for r, read in enumerate(request.reads):
        for call in read.calls:
            j = site_index[call.site]
            if call.allele == "unknown":
                n_unknown += 1
                continue
            alleles[r, j] = ALLELE_REF if call.allele == "ref" else ALLELE_ALT
            quals[r, j] = call.qual

    return NormalizedDataset(
        sample_id=request.sample_id,
        site_ids=site_ids,
        site_chroms=[s.chrom for s in request.sites],
        site_positions=[s.position for s in request.sites],
        ref_bases=[s.ref.upper() for s in request.sites],
        alt_bases=[s.alt.upper() for s in request.sites],
        read_ids=[r.id for r in request.reads],
        alleles=alleles,
        quals=quals,
        observed=alleles != ALLELE_UNKNOWN,
        n_unknown_calls=n_unknown,
    )
