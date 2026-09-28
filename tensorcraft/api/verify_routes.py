"""Verification routes: built-in scenarios, custom scenarios, memory checks."""

from __future__ import annotations

from fastapi import APIRouter

from ..validation import fixtures, run_all_checks, run_scenario
from .schemas import ScenarioRequest


def register_validation_routes(router: APIRouter) -> None:

    @router.get("/verify/scenarios", tags=["verification"])
    async def list_scenarios() -> dict:
        return {"ok": True, "scenarios": sorted(fixtures.SCENARIOS)}

    @router.get("/verify/scenarios/{name}", tags=["verification"])
    async def run_named_scenario(name: str) -> dict:
        if name not in fixtures.SCENARIOS:
            from ..errors import StateError
            raise StateError(
                f"unknown scenario {name!r}",
                details={"handle": name, "not_found": True,
                         "available": sorted(fixtures.SCENARIOS)})
        report = run_scenario(fixtures.SCENARIOS[name])
        body = report.to_dict()
        body["ok"] = not report.failures
        body["scenario"] = name
        return body

    @router.post("/verify/scenarios", tags=["verification"])
    async def run_custom_scenario(payload: ScenarioRequest) -> dict:
        report = run_scenario(payload.steps)
        body = report.to_dict()
        body["ok"] = not report.failures
        return body

    @router.get("/verify/all", tags=["verification"])
    async def verify_all() -> dict:
        scenario_reports = {
            name: run_scenario(steps).to_dict()
            for name, steps in fixtures.SCENARIOS.items()
            if "write_tempcopy" not in name
            and "write_rejected" not in name
        }
        memory_checks = [check.to_dict() for check in run_all_checks()]
        scenario_failures = sum(
            1 for body in scenario_reports.values()
            if body["summary"]["failed"] > 0)
        memory_failures = sum(
            1 for check in memory_checks if not check["passed"])
        return {
            "ok": scenario_failures == 0 and memory_failures == 0,
            "scenarios": scenario_reports,
            "memory_checks": memory_checks,
            "totals": {
                "scenario_failures": scenario_failures,
                "memory_check_failures": memory_failures,
            },
        }

    @router.get("/verify/memory-checks", tags=["verification"])
    async def memory_checks() -> dict:
        checks = [check.to_dict() for check in run_all_checks()]
        return {"ok": all(check["passed"] for check in checks),
                "checks": checks}
