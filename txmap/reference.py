"""Reference sequence handling: synthetic contig generation and FASTA parsing.

The synthetic contig uses the deterministic pattern "ACGT" repeated, so the
base at any position is computable by hand: base(i) = "ACGT"[i % 4]. This is
what makes hand-derived test oracles possible without the implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from .models import Contig

SYNTHETIC_PATTERN = "ACGT"

_COMPLEMENT = str.maketrans("ACGTNacgtn", "TGCANtgcan")


def synthetic_sequence(length: int) -> str:
    """Deterministic synthetic reference: 'ACGT' repeated to `length` bases."""
    if length <= 0:
        raise ValueError("length must be positive")
    repeats, rem = divmod(length, len(SYNTHETIC_PATTERN))
    return SYNTHETIC_PATTERN * repeats + SYNTHETIC_PATTERN[:rem]


def complement(base: str) -> str:
    return base.translate(_COMPLEMENT)


def reverse_complement(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def parse_fasta(path: Path) -> Dict[str, Contig]:
    """Parse a (small) FASTA file into contigs. Bases are upper-cased."""
    contigs: Dict[str, Contig] = {}
    name: str | None = None
    chunks: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if name is not None:
                    contigs[name] = Contig(name=name, sequence="".join(chunks))
                name = line[1:].split()[0]
                if not name:
                    raise ValueError(f"{path}:{lineno}: empty FASTA header")
                chunks = []
            else:
                if name is None:
                    raise ValueError(f"{path}:{lineno}: sequence before header")
                seq = line.upper()
                invalid = set(seq) - set("ACGTN")
                if invalid:
                    raise ValueError(
                        f"{path}:{lineno}: invalid bases {sorted(invalid)}"
                    )
                chunks.append(seq)
    if name is not None:
        contigs[name] = Contig(name=name, sequence="".join(chunks))
    if not contigs:
        raise ValueError(f"{path}: no contigs parsed")
    return contigs


def write_fasta(contigs: Dict[str, Contig], path: Path, width: int = 60) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for contig in contigs.values():
            fh.write(f">{contig.name}\n")
            for i in range(0, contig.length, width):
                fh.write(contig.sequence[i : i + width] + "\n")
