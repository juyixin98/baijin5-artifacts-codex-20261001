"""Configuration layer.

All algorithm-defining choices are frozen into an immutable, validated
``MinimizerConfig``:

- ``k`` / ``window``          : k-mer length and window length (in k-mers)
- ``hash_name``               : named, fixed hash function ("mix64" default,
                                "identity" exposed for hand-verifiable tests)
- ``hash_seed``               : fixed 64-bit seed folded into the hash
- ``tie_break``               : fixed "rightmost" (documented, not optional
                                in behaviour; the field exists so the value
                                is explicit in provenance records)
- ``max_hash_occurrences``    : low-complexity burst cap; a minimizer hash
                                occurring more than this many times across
                                the whole index build is dropped entirely.

Validation failures raise :class:`InvalidParameterError` — incompatible
parameters are a defined failure category, never silently clamped.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .errors import InvalidParameterError

#: Hash functions shipped with the package. Each maps a canonical 2-bit
#: k-mer integer (and a seed) to a 64-bit hash. The registry is closed:
#: unknown names are a parameter error.
HASH_NAMES = ("mix64", "identity")

MIN_K = 2
#: 2-bit encoding in a 64-bit word caps k at 31 (canonical form needs the
#: full k-mer plus headroom for the reverse complement).
MAX_K = 31
MIN_WINDOW = 1
MAX_WINDOW = 256
MIN_OCCURRENCE_CAP = 1

TIE_BREAK = "rightmost"


@dataclass(frozen=True)
class MinimizerConfig:
    k: int = 7
    window: int = 4
    hash_name: str = "mix64"
    hash_seed: int = 0
    max_hash_occurrences: int = 100
    tie_break: str = TIE_BREAK

    def __post_init__(self) -> None:
        if not isinstance(self.k, int) or not MIN_K <= self.k <= MAX_K:
            raise InvalidParameterError(
                f"k must be an integer in [{MIN_K}, {MAX_K}], got {self.k!r}",
                detail={"parameter": "k", "value": self.k},
            )
        if not isinstance(self.window, int) or not MIN_WINDOW <= self.window <= MAX_WINDOW:
            raise InvalidParameterError(
                f"window must be an integer in [{MIN_WINDOW}, {MAX_WINDOW}], got {self.window!r}",
                detail={"parameter": "window", "value": self.window},
            )
        if self.hash_name not in HASH_NAMES:
            raise InvalidParameterError(
                f"unknown hash {self.hash_name!r}; available: {sorted(HASH_NAMES)}",
                detail={"parameter": "hash_name", "value": self.hash_name},
            )
        if not isinstance(self.hash_seed, int) or not 0 <= self.hash_seed < 2**64:
            raise InvalidParameterError(
                f"hash_seed must be an unsigned 64-bit integer, got {self.hash_seed!r}",
                detail={"parameter": "hash_seed", "value": self.hash_seed},
            )
        if not isinstance(self.max_hash_occurrences, int) or self.max_hash_occurrences < MIN_OCCURRENCE_CAP:
            raise InvalidParameterError(
                "max_hash_occurrences must be an integer >= "
                f"{MIN_OCCURRENCE_CAP}, got {self.max_hash_occurrences!r}",
                detail={"parameter": "max_hash_occurrences", "value": self.max_hash_occurrences},
            )
        if self.tie_break != TIE_BREAK:
            raise InvalidParameterError(
                f"tie_break is fixed to {TIE_BREAK!r}, got {self.tie_break!r}",
                detail={"parameter": "tie_break", "value": self.tie_break},
            )

    @property
    def min_sequence_length(self) -> int:
        """Shortest sequence that yields at least one full window."""
        return self.k + self.window - 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MinimizerConfig":
        if not isinstance(data, dict):
            raise InvalidParameterError("config must be a JSON object")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise InvalidParameterError(
                f"unknown config keys: {sorted(unknown)}",
                detail={"unknown_keys": sorted(unknown)},
            )
        return cls(**data)

    def fingerprint(self) -> str:
        """Stable string form used in provenance records."""
        return json.dumps(self.to_dict(), sort_keys=True)
