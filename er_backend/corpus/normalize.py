"""Name normalization: unicode, legal-form stripping, alias / cross-lingual
token mapping.

The normalizer is fixture-driven: the token map (e.g. {"北京": "beijing",
"株式会社": ""}) is supplied by the caller, so tests and deployments control
the exact cross-lingual / alias behaviour without touching this module.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping

from ..errors import InputValidationError

# Latin legal-form tokens removed at token level.
LATIN_LEGAL_TOKENS = frozenset(
    {
        "ltd", "limited", "inc", "incorporated", "corp", "corporation",
        "co", "company", "llc", "llp", "lp", "gmbh", "sarl", "sa", "bv",
        "nv", "pty", "plc", "ag", "kg", "ohg",
    }
)

# CJK legal-form suffixes stripped from the end of CJK runs.
CJK_LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "株式会社",
    "合同会社",
    "有限会社",
)

_CJK_RE = re.compile(r"[一-鿿぀-ヿ]+")
_NON_ALNUM_RE = re.compile(r"[^\w一-鿿぀-ヿ]+", re.UNICODE)


@dataclass(frozen=True)
class NormalizedName:
    original: str
    normalized: str  # canonical space-joined token string
    tokens: tuple[str, ...]


class Normalizer:
    """Normalize organization names into comparable token tuples."""

    def __init__(self, token_map: Mapping[str, str] | None = None) -> None:
        # Longest keys first so multi-char replacements win.
        self._token_map = dict(
            sorted((token_map or {}).items(), key=lambda kv: -len(kv[0]))
        )

    def normalize(self, name: str) -> NormalizedName:
        if not name or not name.strip():
            raise InputValidationError("name must be non-empty")
        text = unicodedata.normalize("NFKC", name).casefold()
        # Apply the caller-supplied token map on the raw text so multi-char
        # CJK keys (北京 -> beijing) match before tokenization.
        for src, dst in self._token_map.items():
            src_cf = unicodedata.normalize("NFKC", src).casefold()
            text = text.replace(src_cf, f" {dst} " if dst else " ")
        text = _NON_ALNUM_RE.sub(" ", text)
        tokens: list[str] = []
        for chunk in text.split():
            tokens.extend(self._expand_chunk(chunk))
        # Strip latin legal-form tokens.
        tokens = [t for t in tokens if t not in LATIN_LEGAL_TOKENS]
        tokens = [t for t in tokens if t]
        if not tokens:
            raise InputValidationError(
                "name normalizes to nothing (only legal-form tokens?)",
                details={"name": name},
            )
        return NormalizedName(
            original=name, normalized=" ".join(tokens), tokens=tuple(tokens)
        )

    @staticmethod
    def _expand_chunk(chunk: str) -> list[str]:
        """Split a whitespace-delimited chunk into tokens.

        Latin/digit chunks stay whole; CJK runs get legal suffixes stripped
        and are then split into characters (unigram fallback for names with
        no token-map coverage).
        """
        out: list[str] = []
        pos = 0
        for m in _CJK_RE.finditer(chunk):
            if m.start() > pos:
                out.append(chunk[pos : m.start()])
            run = m.group(0)
            for suffix in CJK_LEGAL_SUFFIXES:
                if run.endswith(suffix):
                    run = run[: -len(suffix)]
                    break
            out.extend(c for c in run if c.strip())
            pos = m.end()
        if pos < len(chunk):
            out.append(chunk[pos:])
        return [t for t in out if t]
