"""SASLprep profile (RFC 4013) of the stringprep algorithm (RFC 3454).

SCRAM (RFC 5802 section 2.2) requires passwords and usernames to be prepared
with SASLprep before use.  We implement the profile directly on top of the
standard-library :mod:`stringprep` tables instead of pulling in a third-party
dependency for this small, well-defined transform.

Only Unicode ``str`` input is accepted; callers decode transport bytes first.
"""
from __future__ import annotations

import unicodedata
import stringprep
from collections.abc import Callable

# Prohibited tables for SASLprep stored strings (RFC 4013 section 2.3):
# C.2.1, C.2.2, C.3, C.4, C.5, C.6, C.8, C.9. C.1.2 (non-ASCII space) and
# B.1 are handled in the mapping step; C.7 is not prohibited by this profile.
_PROHIBIT_CHECKS: tuple[Callable[[str], bool], ...] = (
    stringprep.in_table_c21,
    stringprep.in_table_c22,
    stringprep.in_table_c3,
    stringprep.in_table_c4,
    stringprep.in_table_c5,
    stringprep.in_table_c6,
    stringprep.in_table_c8,
    stringprep.in_table_c9,
)


class SaslprepError(ValueError):
    """Raised when input contains a prohibited code point or violates bidi rules."""


def _mapped(ch: str) -> str:
    # RFC 4013 section 2.1 mapping:
    #   B.1  (e.g. SOFT HYPHEN)              -> mapped to nothing
    #   C.1.1 ASCII space (U+0020)           -> U+0020 (unchanged)
    #   C.1.2 non-ASCII space (e.g. U+00A0)  -> U+0020
    # stdlib stringprep tables take a single-character string, not a code point.
    if stringprep.in_table_b1(ch):
        return ""
    if stringprep.in_table_c12(ch):
        return " "
    return ch


def sasl_prep(label: str) -> str:
    """Apply SASLprep to *label*.

    Raises :class:`TypeError` for non-str input and :class:`SaslprepError`
    for prohibited output or failed bidirectional checks.
    """
    if not isinstance(label, str):
        raise TypeError(f"SASLprep expects str, got {type(label).__name__}")

    # 1. Mapping
    mapped = "".join(_mapped(ch) for ch in label)
    # 2. Normalization (NFKC)
    normalized = unicodedata.normalize("NFKC", mapped)
    # 3. Prohibited output
    for ch in normalized:
        if any(check(ch) for check in _PROHIBIT_CHECKS):
            raise SaslprepError(f"prohibited character U+{ord(ch):04X} in SASLprep output")
    # 4. Bidirectional character checks (RFC 3454 section 6).
    if normalized and any(stringprep.in_table_d1(ch) for ch in normalized):
        # String contains R/AL: first and last char must be R/AL, no L allowed.
        if not (
            stringprep.in_table_d1(normalized[0])
            and stringprep.in_table_d1(normalized[-1])
            and not any(stringprep.in_table_d2(ch) for ch in normalized)
        ):
            raise SaslprepError("bidirectional string fails RFC 3454 section 6 checks")
    return normalized
