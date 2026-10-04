"""Runtime configuration.  Everything is local; no external accounts."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .encoding import EncodingParams


@dataclass(frozen=True)
class Settings:
    database_path: str = "paillier_service.db"
    key_size: int = 2048
    # Test-only switch: when true, clients may attach the plaintext fixture
    # to a contribution so the independent verifier can recompute the
    # plaintext reference.  This exists solely for local review/testing and
    # would be removed in any real deployment.
    accept_plaintext_fixtures: bool = True
    encoding: EncodingParams = field(default_factory=EncodingParams)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.environ.get("PAILLIER_DB_PATH", "paillier_service.db"),
            key_size=int(os.environ.get("PAILLIER_KEY_SIZE", "2048")),
            accept_plaintext_fixtures=os.environ.get(
                "PAILLIER_ACCEPT_PLAINTEXT_FIXTURES", "true"
            ).lower()
            == "true",
            encoding=EncodingParams(
                max_plaintext_abs=int(
                    os.environ.get("PAILLIER_MAX_PLAINTEXT_ABS", "1000000")
                ),
                max_coefficient_abs=int(
                    os.environ.get("PAILLIER_MAX_COEFFICIENT_ABS", "1000")
                ),
                max_aggregate_abs=int(
                    os.environ.get("PAILLIER_MAX_AGGREGATE_ABS", "1000000000")
                ),
            ),
        )
