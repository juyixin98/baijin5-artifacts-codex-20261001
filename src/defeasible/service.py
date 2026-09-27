"""Application service: orchestrates storage, the reasoning kernel, logging.

This is the only layer that talks to all three others, which keeps the API
thin and the engine/storage independently testable.
"""

from __future__ import annotations

from typing import Any

from .engine import Engine, Evaluation
from .errors import DefeasibleError
from .language import Term, Theory, validate_theory
from .logging import RunLogger, new_run_id
from .storage import EvidenceStore


class ReasoningService:
    def __init__(
        self,
        store: EvidenceStore,
        engine: Engine,
        logger: RunLogger,
    ) -> None:
        self.store = store
        self.engine = engine
        self.logger = logger

    # ----- case / theory / evidence management -----

    def create_case(self, case_id: str, description: str = "") -> dict:
        self.store.create_case(case_id, description)
        return {"case_id": case_id, "description": description}

    def list_cases(self) -> list[dict]:
        return self.store.list_cases()

    def put_theory(self, case_id: str, theory_dict: dict) -> dict:
        """Parse + validate, then persist.  Validation errors are raised."""
        theory = Theory.from_dict(theory_dict)
        validate_theory(theory)  # raises InvalidInput / TheoryConflict(cycle)
        self.store.ensure_case(case_id)
        self.store.save_theory(case_id, theory.to_dict())
        return {"case_id": case_id, "rules": len(theory.rules),
                "priorities": len(theory.priorities)}

    def add_evidence(self, case_id: str, literals: list[str]) -> dict:
        terms = [Term.parse(l) for l in literals]
        n = self.store.add_evidence(case_id, terms)
        return {"case_id": case_id, "added": n}

    def case_snapshot(self, case_id: str) -> dict[str, Any]:
        evidence = self.store.load_evidence(case_id)
        theory_dict = self.store.load_theory(case_id)
        return {
            "case_id": case_id,
            "theory": theory_dict,
            "evidence": [e.literal for e in evidence],
        }

    # ----- reasoning -----

    def _load(self, case_id: str) -> tuple[Theory, list[Term]]:
        theory_dict = self.store.load_theory(case_id)
        if theory_dict is None:
            from .errors import InvalidInputError

            raise InvalidInputError(
                f"case {case_id!r} has no theory; PUT one first"
            )
        theory = Theory.from_dict(theory_dict)
        evidence = self.store.load_evidence(case_id)
        return theory, evidence

    def evaluate_case(self, case_id: str) -> Evaluation:
        theory, evidence = self._load(case_id)
        return self.engine.evaluate(theory, evidence)

    def query(self, case_id: str, literal: str) -> dict[str, Any]:
        """Run one query with full replay logging. Never swallows errors."""
        run_id = new_run_id()
        self.logger.log_start(run_id, case_id, literal)
        try:
            term = Term.parse(literal)  # may raise InvalidInputError
            theory, evidence = self._load(case_id)
            evaluation = self.engine.evaluate(theory, evidence)
            self.logger.log_intermediate(run_id, evaluation)
            result = evaluation.query(term)
            self.logger.log_result(run_id, result)
            return {
                "run_id": run_id,
                **result.to_dict(),
                "evaluation": evaluation.to_dict(),
            }
        except DefeasibleError as e:
            self.logger.log_error(run_id, e)
            raise
        except Exception as e:  # genuine computational failure, made distinct
            from .errors import ComputationError

            err = ComputationError(str(e))
            self.logger.log_error(run_id, err)
            raise err from e

    def replay(self, run_id: str) -> dict[str, Any]:
        records = self.logger.records_for(run_id)
        if not records:
            from .errors import InvalidInputError

            raise InvalidInputError(f"unknown run_id {run_id!r}")
        return {"run_id": run_id, "records": records}
