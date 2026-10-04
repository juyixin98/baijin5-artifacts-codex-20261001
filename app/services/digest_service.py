"""Digest orchestration: validation, version stamping, timing, provenance."""

from __future__ import annotations

import time
import uuid
from typing import Any

from app import ENZYME_CATALOG_VERSION, MASS_TABLE_VERSION, __version__
from app.config import Settings
from app.domain.digestion import digest
from app.domain.parsing import parse_sequence
from app.domain.rules import Enzyme, load_enzyme_table
from app.errors import (
    DigestError,
    EnzymeNotFoundError,
    InvalidMissedCleavageError,
)
from app.logging_setup import RunLogger
from app.persistence.repository import RunRepository


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


class DigestService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enzymes: dict[str, Enzyme] = load_enzyme_table(settings.enzyme_table_path)
        self.repository = RunRepository(settings.db_path)

    def version_context(self) -> dict[str, str]:
        return {
            "app_version": __version__,
            "mass_table_version": MASS_TABLE_VERSION,
            "enzyme_catalog_version": ENZYME_CATALOG_VERSION,
        }

    def list_enzymes(self) -> list[dict[str, Any]]:
        return [enzyme.to_dict() for enzyme in self.enzymes.values()]

    def get_enzyme(self, name: str) -> Enzyme:
        enzyme = self.enzymes.get(name)
        if enzyme is None:
            raise EnzymeNotFoundError(
                f"enzyme {name!r} not in the local synthetic catalog",
                {"requested": name, "available": sorted(self.enzymes)},
            )
        return enzyme

    def run_digest(
        self,
        raw_sequence: str,
        enzyme_name: str,
        missed_cleavages: int,
        run_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        run_id = run_id or new_run_id()
        run_log = RunLogger("digest", run_id)
        started = time.perf_counter()
        versions = self.version_context()
        run_log.step(
            "run_started",
            f"digest run started: enzyme={enzyme_name} missed_cleavages={missed_cleavages}",
            enzyme=enzyme_name,
            missed_cleavages=missed_cleavages,
            versions=versions,
            sequence_chars=len(raw_sequence) if isinstance(raw_sequence, str) else None,
        )

        try:
            enzyme = self.get_enzyme(enzyme_name)
            self._validate_missed(missed_cleavages)
            parsed = parse_sequence(raw_sequence, self.settings.max_sequence_length)
            run_log.step(
                "sequence_parsed",
                f"parsed {parsed.length} residues; N-boundary=0, C-boundary={parsed.length}",
                length=parsed.length,
                n_term_offset=0,
                c_term_offset=parsed.length,
            )
            result = digest(
                parsed.sequence,
                enzyme,
                missed_cleavages,
                mass_decimals=self.settings.mass_decimals,
                run_log=run_log,
            )
            result_dict = result.to_dict()
            duration_ms = (time.perf_counter() - started) * 1000.0
            if persist:
                self.repository.save_success(
                    run_id, parsed.sequence, enzyme_name, missed_cleavages,
                    result_dict, versions, duration_ms,
                )
            run_log.step("run_succeeded", "digest run succeeded",
                         fragment_count=len(result.fragments), duration_ms=duration_ms)
            return {
                "success": True,
                "run_id": run_id,
                "versions": versions,
                "input": {
                    "sequence": parsed.sequence,
                    "sequence_length": parsed.length,
                    "enzyme": enzyme_name,
                    "missed_cleavages": missed_cleavages,
                },
                "result": result_dict,
            }
        except DigestError as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            run_log.failure(
                "run_failed",
                f"digest run failed: {exc.code}: {exc.message}",
                error_code=exc.code,
                error_message=exc.message,
                details=exc.details,
                duration_ms=duration_ms,
            )
            if persist:
                # Sequence may itself be invalid; persist raw input when stringy.
                safe_seq = raw_sequence if isinstance(raw_sequence, str) else None
                self.repository.save_failure(
                    run_id, safe_seq, enzyme_name,
                    missed_cleavages if isinstance(missed_cleavages, int) else None,
                    exc.code, exc.message, versions, duration_ms,
                )
            raise

    def _validate_missed(self, missed_cleavages: int) -> None:
        if not isinstance(missed_cleavages, int) or isinstance(missed_cleavages, bool):
            raise InvalidMissedCleavageError(
                "missed_cleavages must be a non-negative integer",
                {"received": repr(missed_cleavages)},
            )
        if missed_cleavages < 0:
            raise InvalidMissedCleavageError(
                "missed_cleavages must be >= 0", {"received": missed_cleavages}
            )
        if missed_cleavages > self.settings.max_missed_cleavages:
            raise InvalidMissedCleavageError(
                f"missed_cleavages {missed_cleavages} exceeds configured maximum "
                f"{self.settings.max_missed_cleavages}",
                {"received": missed_cleavages,
                 "max": self.settings.max_missed_cleavages},
            )

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.repository.get_run(run_id)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.list_runs(limit=limit)
