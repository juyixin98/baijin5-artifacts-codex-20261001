"""Independent verification layer.

Deliberately does NOT reuse the service's aggregation or decode code:
  * re-aggregates ciphertexts with its own modular arithmetic
    (prod of pow(c, w mod n, n^2) mod n^2),
  * decrypts with a textbook Paillier implementation
    (lambda = lcm(p-1, q-1), mu = lambda^-1 mod n, g = n + 1),
  * decodes signed values with its own bounds check,
  * recomputes the plaintext reference from the stored plaintext fixtures
    with plain Python integer arithmetic.

A verification report is PASS only when every applicable check agrees.
Missing fixtures yield UNVERIFIABLE, never a silent pass.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from .encoding import EncodingParams
from .errors import ErrorCategory, PaillierServiceError
from .service import AggregationService
from .storage import Storage

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNVERIFIABLE = "UNVERIFIABLE"


def _independent_decrypt(ciphertext: int, n: int, p: int, q: int) -> int:
    """Textbook Paillier decryption with g = n + 1 (no phe involved)."""
    nsquare = n * n
    lam = math.lcm(p - 1, q - 1)
    mu = pow(lam, -1, n)
    x = pow(ciphertext, lam, nsquare)
    return ((x - 1) // n) * mu % n


def _independent_decode(raw: int, n: int, max_aggregate_abs: int) -> int | None:
    """Signed decode; returns None for the ambiguous zone instead of raising."""
    if raw <= max_aggregate_abs:
        return raw
    if raw >= n - max_aggregate_abs:
        return raw - n
    return None


def _independent_aggregate(
    contributions: list[tuple[int, int]], n: int
) -> int:
    """Weighted ciphertext sum via raw modular arithmetic."""
    nsquare = n * n
    acc = 1  # neutral element: encryption of 0
    for ciphertext, coefficient in contributions:
        weighted = pow(ciphertext, coefficient % n, nsquare)
        acc = (acc * weighted) % nsquare
    return acc


@dataclass
class VerificationReport:
    batch_id: str
    status: str
    checks: list[dict] = field(default_factory=list)
    expected_plaintext: int | None = None
    service_plaintext: int | None = None
    independent_plaintext: int | None = None

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "checks": self.checks,
            "expected_plaintext": self.expected_plaintext,
            "service_plaintext": self.service_plaintext,
            "independent_plaintext": self.independent_plaintext,
        }


class IndependentVerifier:
    def __init__(self, storage: Storage):
        self.storage = storage

    def verify(self, batch_id: str) -> VerificationReport:
        batch = self.storage.get_batch(batch_id)
        if batch is None:
            raise PaillierServiceError(
                ErrorCategory.BATCH_NOT_FOUND,
                f"unknown batch {batch_id!r}",
                {"batch_id": batch_id},
            )
        aggregate = self.storage.get_aggregate(batch_id)
        if aggregate is None:
            raise PaillierServiceError(
                ErrorCategory.BATCH_STATE_INVALID,
                "batch has no aggregate to verify",
                {"batch_id": batch_id},
            )

        params = EncodingParams.from_dict(json.loads(batch["encoding_params"]))
        n = int(batch["public_n"])
        rows = self.storage.list_contributions(batch_id)
        report = VerificationReport(batch_id=batch_id, status=STATUS_PASS)

        # Check 1: ciphertext consistency — independent re-aggregation must
        # reproduce the stored aggregate ciphertext bit-for-bit.
        pairs = [(int(r["ciphertext"]), int(r["coefficient"])) for r in rows]
        recomputed = _independent_aggregate(pairs, n)
        stored = int(aggregate["result_ciphertext"])
        report.checks.append(
            {
                "name": "ciphertext_reaggregation",
                "ok": recomputed == stored,
                "basis": "prod(pow(c_i, w_i mod n, n^2)) mod n^2 == stored aggregate",
            }
        )

        # Check 2: independent decryption + decode of the stored aggregate.
        independent_plaintext = None
        if batch["private_p"] is not None:
            raw = _independent_decrypt(
                stored, n, int(batch["private_p"]), int(batch["private_q"])
            )
            independent_plaintext = _independent_decode(
                raw, n, params.max_aggregate_abs
            )
            report.independent_plaintext = independent_plaintext
            report.checks.append(
                {
                    "name": "independent_decrypt_decode",
                    "ok": independent_plaintext is not None,
                    "basis": "textbook Paillier decrypt + bounded signed decode",
                    "raw_residue": str(raw),
                }
            )

        # Check 3: plaintext reference from fixtures (test-only data).
        fixtures = [r["plaintext_fixture"] for r in rows]
        if all(f is not None for f in fixtures) and fixtures:
            expected = sum(
                int(r["coefficient"]) * int(r["plaintext_fixture"]) for r in rows
            )
            report.expected_plaintext = expected
            report.checks.append(
                {
                    "name": "plaintext_reference",
                    "ok": True,
                    "basis": "sum(w_i * m_i) over stored plaintext fixtures",
                    "expected": expected,
                }
            )
        else:
            report.checks.append(
                {
                    "name": "plaintext_reference",
                    "ok": False,
                    "basis": "plaintext fixtures missing; cannot recompute reference",
                }
            )

        # Check 4: cross-agreement of every value we managed to derive.
        service_plaintext = aggregate["decrypted_plaintext"]
        report.service_plaintext = (
            int(service_plaintext) if service_plaintext is not None else None
        )
        derived = [
            v
            for v in (
                report.expected_plaintext,
                report.independent_plaintext,
                report.service_plaintext,
            )
            if v is not None
        ]
        agreement = len(derived) >= 2 and all(v == derived[0] for v in derived)
        report.checks.append(
            {
                "name": "cross_agreement",
                "ok": agreement,
                "basis": "all derivable plaintexts must be equal",
                "values": derived,
            }
        )

        failed = [c for c in report.checks if not c["ok"]]
        if report.expected_plaintext is None:
            report.status = STATUS_UNVERIFIABLE
        elif failed:
            report.status = STATUS_FAIL
        else:
            report.status = STATUS_PASS
        return report


def verify_batch(service: AggregationService, run_id: str, batch_id: str) -> dict:
    report = IndependentVerifier(service.storage).verify(batch_id)
    service.audit.record(
        run_id,
        "VERIFICATION_DONE",
        batch_id,
        status=report.status,
        checks=report.checks,
    )
    return report.to_dict()
