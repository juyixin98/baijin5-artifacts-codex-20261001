"""Protocol encoding layer: signed integers inside the Paillier plaintext group.

Paillier plaintexts live in Z_n (unsigned residues).  Signed values are
encoded as `value mod n`:

    encode(v) = v mod n            for -max_plaintext_abs <= v <= max_plaintext_abs

Decoding is only unambiguous while the *true* aggregate stays inside
[-max_aggregate_abs, max_aggregate_abs], which the protocol guarantees by
construction (per-contribution plaintext bound + coefficient bound +
server-side worst-case bound tracking).  A residue that falls outside both
`[0, max_aggregate_abs]` and `[n - max_aggregate_abs, n)` is in the
ambiguous zone: modular wraparound has occurred and the value MUST NOT be
silently interpreted as an ordinary negative number — decode raises
DECODE_AMBIGUOUS instead.

Only operations Paillier genuinely supports are represented here:
ciphertext addition and multiplication by a plaintext scalar.  There is no
ciphertext-ciphertext multiplication and no comparison — by design.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from .errors import ErrorCategory, PaillierServiceError


@dataclass(frozen=True)
class EncodingParams:
    """Encoding parameters bound to a batch at creation time.

    max_plaintext_abs:   |plaintext| per contribution must be <= this.
    max_coefficient_abs: |weight| per contribution must be <= this.
    max_aggregate_abs:   |weighted sum| must stay <= this; also defines the
                         ambiguous decode zone and must satisfy
                         2 * max_aggregate_abs < n.
    """

    max_plaintext_abs: int = 1_000_000
    max_coefficient_abs: int = 1_000
    max_aggregate_abs: int = 1_000_000_000

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EncodingParams":
        return cls(
            max_plaintext_abs=int(data["max_plaintext_abs"]),
            max_coefficient_abs=int(data["max_coefficient_abs"]),
            max_aggregate_abs=int(data["max_aggregate_abs"]),
        )

    def validate_self(self) -> None:
        for field in ("max_plaintext_abs", "max_coefficient_abs", "max_aggregate_abs"):
            if getattr(self, field) < 1:
                raise PaillierServiceError(
                    ErrorCategory.ENCODING_PARAM_INVALID,
                    f"{field} must be >= 1",
                    {"params": self.to_dict()},
                )
        if self.max_aggregate_abs < self.max_plaintext_abs:
            raise PaillierServiceError(
                ErrorCategory.ENCODING_PARAM_INVALID,
                "max_aggregate_abs must be >= max_plaintext_abs "
                "(a single unweighted contribution must be representable)",
                {"params": self.to_dict()},
            )

    def validate_against_modulus(self, n: int) -> None:
        """The ambiguous zone only works if it is strictly smaller than n/2."""
        self.validate_self()
        if 2 * self.max_aggregate_abs >= n:
            raise PaillierServiceError(
                ErrorCategory.ENCODING_PARAM_INVALID,
                "encoding bounds too large for modulus: "
                "require 2 * max_aggregate_abs < n",
                {"params": self.to_dict(), "modulus_bits": n.bit_length()},
            )


def encode_signed(value: int, n: int, params: EncodingParams) -> int:
    """Encode a signed plaintext into Z_n.  Raises PLAINTEXT_OUT_OF_RANGE."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise PaillierServiceError(
            ErrorCategory.PLAINTEXT_OUT_OF_RANGE,
            "plaintext must be an integer",
            {"value": repr(value)},
        )
    if abs(value) > params.max_plaintext_abs:
        raise PaillierServiceError(
            ErrorCategory.PLAINTEXT_OUT_OF_RANGE,
            f"plaintext {value} exceeds bound +/-{params.max_plaintext_abs}",
            {"value": value, "max_plaintext_abs": params.max_plaintext_abs},
        )
    return value % n


def decode_signed(raw: int, n: int, params: EncodingParams) -> int:
    """Decode a residue from Z_n back to a signed integer.

    Raises DECODE_AMBIGUOUS when the residue lies in the ambiguous zone —
    i.e. the true sum left the guaranteed range and modular wraparound
    occurred.  Such a residue is never reported as an ordinary negative.
    """
    if not 0 <= raw < n:
        raise PaillierServiceError(
            ErrorCategory.CIPHERTEXT_INVALID,
            "raw plaintext is not a residue mod n",
            {"raw": raw},
        )
    bound = params.max_aggregate_abs
    if raw <= bound:
        return raw
    if raw >= n - bound:
        return raw - n
    raise PaillierServiceError(
        ErrorCategory.DECODE_AMBIGUOUS,
        "decrypted residue lies in the ambiguous zone: the aggregate "
        "exceeded max_aggregate_abs and modular wraparound occurred; "
        "refusing to interpret it as an ordinary value",
        {"raw_residue": raw, "max_aggregate_abs": bound},
    )


def check_coefficient(coefficient: int, params: EncodingParams) -> None:
    if isinstance(coefficient, bool) or not isinstance(coefficient, int):
        raise PaillierServiceError(
            ErrorCategory.COEFFICIENT_OUT_OF_RANGE,
            "coefficient must be an integer",
            {"coefficient": repr(coefficient)},
        )
    if abs(coefficient) > params.max_coefficient_abs:
        raise PaillierServiceError(
            ErrorCategory.COEFFICIENT_OUT_OF_RANGE,
            f"coefficient {coefficient} exceeds bound "
            f"+/-{params.max_coefficient_abs}",
            {
                "coefficient": coefficient,
                "max_coefficient_abs": params.max_coefficient_abs,
            },
        )


def worst_case_contribution(coefficient: int, params: EncodingParams) -> int:
    """Worst-case |weighted plaintext| for one contribution."""
    return abs(coefficient) * params.max_plaintext_abs
