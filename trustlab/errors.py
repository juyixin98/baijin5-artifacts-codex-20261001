"""Error taxonomy for the trust-bundle rotation test platform.

Four top-level categories are kept strictly distinguishable everywhere:
audit rows, handshake-failure records, HTTP responses and frame-level
errors all carry one of these values, so a replayed run can tell *why*
something failed:

- INPUT_ERROR:        caller supplied malformed / unparsable input.
- STATE_CONFLICT:     input parsed fine but conflicts with persisted state
                      (e.g. bundle version reuse, illegal activation).
- RESOURCE_EXHAUSTED: a configured limit was hit (connections, frame size).
- CRYPTO_FAILURE:     cryptographic verification or TLS handshake failure.

Fine-grained handshake/verify causes are expressed as FailureClass values
inside the CRYPTO_FAILURE category.
"""
from __future__ import annotations

import enum


class ErrorCategory(str, enum.Enum):
    INPUT_ERROR = "INPUT_ERROR"
    STATE_CONFLICT = "STATE_CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    CRYPTO_FAILURE = "CRYPTO_FAILURE"


class FailureClass(str, enum.Enum):
    """Fine-grained classes for CRYPTO_FAILURE handshake/verify failures."""

    CERT_EXPIRED = "CERT_EXPIRED"
    CERT_NOT_YET_VALID = "CERT_NOT_YET_VALID"
    UNTRUSTED_ROOT = "UNTRUSTED_ROOT"
    WRONG_PURPOSE = "WRONG_PURPOSE"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    NO_CLIENT_CERT = "NO_CLIENT_CERT"
    HANDSHAKE_FAILED = "HANDSHAKE_FAILED"


# OpenSSL X509_verify_cert error codes -> FailureClass.
_VERIFY_CODE_MAP = {
    7: FailureClass.SIGNATURE_INVALID,
    9: FailureClass.CERT_NOT_YET_VALID,
    10: FailureClass.CERT_EXPIRED,
    18: FailureClass.UNTRUSTED_ROOT,
    19: FailureClass.UNTRUSTED_ROOT,
    20: FailureClass.UNTRUSTED_ROOT,
    21: FailureClass.UNTRUSTED_ROOT,
    26: FailureClass.WRONG_PURPOSE,
}

# Message-substring fallback for stacks that do not expose verify_code.
_VERIFY_MESSAGE_MAP = (
    ("certificate has expired", FailureClass.CERT_EXPIRED),
    ("certificate is not yet valid", FailureClass.CERT_NOT_YET_VALID),
    ("unsupported certificate purpose", FailureClass.WRONG_PURPOSE),
    ("certificate signature failure", FailureClass.SIGNATURE_INVALID),
    ("unable to get local issuer certificate", FailureClass.UNTRUSTED_ROOT),
    ("self-signed certificate", FailureClass.UNTRUSTED_ROOT),
    ("self signed certificate", FailureClass.UNTRUSTED_ROOT),
    ("unable to verify the first certificate", FailureClass.UNTRUSTED_ROOT),
)


def classify_verify_failure(
    verify_code: int | None, verify_message: str | None
) -> FailureClass:
    """Map an OpenSSL verification failure to a stable FailureClass."""
    if verify_code in _VERIFY_CODE_MAP:
        return _VERIFY_CODE_MAP[verify_code]
    msg = (verify_message or "").lower()
    for needle, cls in _VERIFY_MESSAGE_MAP:
        if needle in msg:
            return cls
    return FailureClass.HANDSHAKE_FAILED


class TrustLabError(Exception):
    """Base error carrying a category, HTTP status and replay reasoning."""

    category: ErrorCategory = ErrorCategory.INPUT_ERROR
    http_status: int = 400

    def __init__(self, message: str, *, detail: dict | None = None,
                 reasoning: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        self.reasoning = reasoning

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
            "reasoning": self.reasoning,
        }


class InputError(TrustLabError):
    category = ErrorCategory.INPUT_ERROR
    http_status = 400


class StateConflict(TrustLabError):
    category = ErrorCategory.STATE_CONFLICT
    http_status = 409


class ResourceExhausted(TrustLabError):
    category = ErrorCategory.RESOURCE_EXHAUSTED
    http_status = 429


class CryptoFailure(TrustLabError):
    category = ErrorCategory.CRYPTO_FAILURE
    http_status = 422
