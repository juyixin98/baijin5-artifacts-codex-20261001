"""Synthetic fixture parsing and validation.

The fixture (data/fixtures/transcripts.json) is the single external data
contract. Parsing fails loudly on any structural problem instead of silently
"repairing" it -- a wrong exon order would corrupt every mapping downstream.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import ValidationError
from .models import Exon, Transcript, VALID_STRANDS

ALLOWED_BASES = frozenset("ACGT")


def _check(condition: bool, detail: str, **state: object) -> None:
    if not condition:
        raise ValidationError(detail, **state)


def parse_fixture(
    path: str | Path,
) -> tuple[dict[str, int], dict[str, Transcript]]:
    """Load and validate the fixture file.

    Returns:
        (chromosomes, transcripts) where chromosomes maps chrom name to its
        synthetic length and transcripts maps transcript id to the model.
    """
    chromosomes, _patterns, transcripts = parse_fixture_full(path)
    return chromosomes, transcripts


def parse_fixture_full(
    path: str | Path,
) -> tuple[dict[str, int], dict[str, str], dict[str, Transcript]]:
    """Like :func:`parse_fixture` but also returns each chromosome's pattern."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"fixture file not found: {path}", path=str(path)) from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "fixture is not valid JSON", path=str(path), line=exc.lineno, col=exc.colno
        ) from exc

    raw_chromosomes = raw.get("chromosomes", [])
    chromosomes = _parse_chromosomes(raw_chromosomes)
    patterns = {
        c["name"]: c.get("pattern", "ACGT") for c in raw_chromosomes
    }
    transcripts = _parse_transcripts(raw.get("transcripts", []), chromosomes)
    return chromosomes, patterns, transcripts


def _parse_chromosomes(raw: object) -> dict[str, int]:
    _check(isinstance(raw, list), "'chromosomes' must be a list")
    chromosomes: dict[str, int] = {}
    for entry in raw:
        _check(isinstance(entry, dict), "each chromosome must be an object", entry=entry)
        name = entry.get("name")
        length = entry.get("length")
        pattern = entry.get("pattern", "ACGT")
        _check(isinstance(name, str) and name, "chromosome 'name' must be a non-empty string")
        _check(isinstance(length, int) and length > 0, "chromosome 'length' must be > 0", name=name)
        _check(
            isinstance(pattern, str) and pattern,
            "chromosome 'pattern' must be a non-empty string",
            name=name,
        )
        invalid = sorted(set(pattern) - ALLOWED_BASES)
        _check(not invalid, "pattern may only contain ACGT", name=name, invalid=invalid)
        _check(name not in chromosomes, "duplicate chromosome name", name=name)
        chromosomes[name] = length
    return chromosomes


def _parse_transcripts(
    raw: object, chromosomes: dict[str, int]
) -> dict[str, Transcript]:
    _check(isinstance(raw, list) and raw, "'transcripts' must be a non-empty list")
    transcripts: dict[str, Transcript] = {}
    for entry in raw:
        _check(isinstance(entry, dict), "each transcript must be an object", entry=entry)
        tx_id = entry.get("id")
        chrom = entry.get("chrom")
        strand = entry.get("strand")
        raw_exons = entry.get("exons")

        _check(isinstance(tx_id, str) and tx_id, "transcript 'id' must be a non-empty string")
        _check(tx_id not in transcripts, "duplicate transcript id", id=tx_id)
        _check(isinstance(chrom, str) and chrom in chromosomes,
               "transcript references unknown chromosome", id=tx_id, chrom=chrom,
               known=sorted(chromosomes))
        _check(strand in VALID_STRANDS, "strand must be '+' or '-'", id=tx_id, strand=strand)
        _check(isinstance(raw_exons, list) and len(raw_exons) >= 1,
               "transcript needs at least one exon", id=tx_id)

        exons = _parse_exons(raw_exons, tx_id, chrom, chromosomes[chrom])  # type: ignore[arg-type]
        transcripts[tx_id] = Transcript(
            transcript_id=tx_id, chrom=chrom, strand=strand, exons=tuple(exons)
        )
    return transcripts


def _parse_exons(
    raw_exons: list, tx_id: str, chrom: str, chrom_length: int
) -> list[Exon]:
    exons: list[Exon] = []
    prev_end = -1
    for i, pair in enumerate(raw_exons):
        _check(
            isinstance(pair, list) and len(pair) == 2
            and all(isinstance(v, int) for v in pair),
            "exon must be a [start, end] pair of integers",
            transcript=tx_id, exon_index=i, exon=pair,
        )
        start, end = pair
        _check(0 <= start < end, "exon must satisfy 0 <= start < end",
               transcript=tx_id, exon_index=i, start=start, end=end)
        _check(end <= chrom_length, "exon extends beyond chromosome length",
               transcript=tx_id, exon_index=i, end=end, chrom_length=chrom_length)
        # Adjacent exons (start == prev_end) are legal: a zero-base gap.
        _check(start >= prev_end, "exons must be ascending and non-overlapping",
               transcript=tx_id, exon_index=i, start=start, previous_end=prev_end)
        exons.append(Exon(start=start, end=end))
        prev_end = end
    return exons
