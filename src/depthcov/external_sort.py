"""External sorting for alignment records larger than memory.

Records use a line-oriented TSV codec with a fixed column order::

    qname \\t ref \\t start \\t mapq \\t qlen \\t strand \\t rg \\t dup \\t cigar

The input is split into bounded-size chunks, each chunk is sorted in memory
and spilled to a NamedTemporaryFile, then all runs are k-way merged with
:mod:`heapq`. Nothing assumes the whole input fits in RAM.
"""

from __future__ import annotations

import heapq
import os
import tempfile
from collections.abc import Iterator

from .models import Alignment, Strand

# Fixed codec version, persisted in every run header for provenance.
CODEC_VERSION = "tsv-aln-v1"
HEADER = "#" + "\t".join(
    ["codec", "qname", "ref", "start", "mapq", "qlen",
     "strand", "rg", "dup", "cigar"]
)


class RecordCodecError(ValueError):
    """Malformed serialized record."""


def encode_record(alignment: Alignment) -> str:
    qlen = "" if alignment.query_length is None else str(alignment.query_length)
    return "\t".join([
        alignment.query_name,
        alignment.ref_name,
        str(alignment.ref_start),
        str(alignment.mapq),
        qlen,
        alignment.strand.value,
        alignment.read_group,
        "1" if alignment.is_duplicate else "0",
        alignment.cigar,
    ])


def decode_record(line: str, *, lineno: int | None = None) -> Alignment:
    line = line.rstrip("\n").rstrip("\r")
    if not line or line.startswith("#"):
        raise RecordCodecError("blank or header line is not a record")
    parts = line.split("\t")
    if len(parts) != 9:
        raise RecordCodecError(
            f"expected 9 tab-separated fields, got {len(parts)}"
            + (f" at line {lineno}" if lineno is not None else "")
        )
    qname, ref, start_s, mapq_s, qlen_s, strand_s, rg, dup_s, cigar = parts
    where = f" at line {lineno}" if lineno is not None else ""
    try:
        start = int(start_s)
        mapq = int(mapq_s)
    except ValueError as exc:
        raise RecordCodecError(f"non-integer coordinate{where}: {exc}") from exc
    try:
        strand = Strand(strand_s)
    except ValueError as exc:
        raise RecordCodecError(f"bad strand {strand_s!r}{where}") from exc
    if dup_s not in {"0", "1"}:
        raise RecordCodecError(f"dup flag must be 0/1{where}, got {dup_s!r}")
    qlen = int(qlen_s) if qlen_s else None
    try:
        return Alignment(
            query_name=qname,
            ref_name=ref,
            ref_start=start,
            cigar=cigar,
            mapq=mapq,
            query_length=qlen,
            strand=strand,
            read_group=rg,
            is_duplicate=dup_s == "1",
        )
    except ValueError as exc:
        raise RecordCodecError(f"invalid record{where}: {exc}") from exc


def sort_key(alignment: Alignment) -> tuple[str, int, str]:
    """Records are merged ordered by (reference, start, qname)."""
    return alignment.ref_name, alignment.ref_start, alignment.query_name


def _write_run(records: list[Alignment], tmp_dir: str) -> str:
    records.sort(key=sort_key)
    fd, path = tempfile.mkstemp(prefix="depthcov-run-", suffix=".tsv", dir=tmp_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(HEADER + "\n")
            handle.write(f"# codec={CODEC_VERSION}\n")
            for record in records:
                handle.write(encode_record(record) + "\n")
    except BaseException:
        os.unlink(path)
        raise
    return path


def _iter_run(path: str) -> Iterator[Alignment]:
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            if line.startswith("#"):
                continue
            yield decode_record(line, lineno=lineno)


def external_sort(
    records: Iterator[Alignment],
    *,
    chunk_size: int = 50_000,
    tmp_dir: str | None = None,
) -> Iterator[Alignment]:
    """Spill sorted runs and yield every record in global sorted order.

    ``chunk_size`` bounds the number of records held in memory per run.
    Run files are deleted as the merge drains them.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    run_paths: list[str] = []
    buffer: list[Alignment] = []
    try:
        for record in records:
            buffer.append(record)
            if len(buffer) >= chunk_size:
                run_paths.append(_write_run(buffer, tmp_dir or tempfile.gettempdir()))
                buffer = []
        if buffer:
            run_paths.append(_write_run(buffer, tmp_dir or tempfile.gettempdir()))
        buffer = []

        if not run_paths:
            return

        streams = [_iter_run(path) for path in run_paths]
        merged = heapq.merge(*streams, key=sort_key)
        for record in merged:
            yield record
    finally:
        buffer = []
        for path in run_paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def parse_tsv_file(path: str) -> Iterator[Alignment]:
    """Stream decode a TSV file, raising :class:`RecordCodecError` on bad rows."""
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            if line.startswith("#") or not line.strip():
                continue
            yield decode_record(line, lineno=lineno)
