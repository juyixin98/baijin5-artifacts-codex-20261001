"""Runtime configuration.

The coordinate basis is deliberately NOT configurable: it is fixed at
0-based half-open [start, end) for every input and output of this service.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# SAM-style flag bits honoured by the quality filter.
FLAG_UNMAPPED = 0x4
FLAG_SECONDARY = 0x100
FLAG_QC_FAIL = 0x200
FLAG_DUPLICATE = 0x400
FLAG_SUPPLEMENTARY = 0x800

#: Hard cap on alignments accepted in a single API request body.
MAX_RECORDS_PER_REQUEST = 1_000_000


@dataclass(frozen=True)
class FilterConfig:
    """Quality-filter thresholds for one coverage run."""

    min_mapq: int = 20
    exclude_secondary: bool = True
    exclude_supplementary: bool = True
    exclude_qc_fail: bool = True
    exclude_duplicates: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PipelineConfig:
    """Knobs for the whole pipeline; persisted into provenance per run."""

    filter: FilterConfig = FilterConfig()
    #: Records held in memory per external-sort run. Inputs larger than this
    #: spill to SQLite-backed sorted runs on disk.
    sort_chunk_size: int = 50_000

    def to_dict(self) -> dict:
        return {
            "filter": self.filter.to_dict(),
            "sort_chunk_size": self.sort_chunk_size,
        }
