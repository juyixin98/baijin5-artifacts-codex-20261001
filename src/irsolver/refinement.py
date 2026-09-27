"""迭代精化主流程：低精度 LU 分解 + 高精度残差校正，逐列独立求解。

算法（每个右端列独立执行）：
1. 在当前精度级分解 A（fp32 -> fp64 -> mpmath 阶梯），解出初始 x；
2. 用原矩阵按该级残差精度计算 r = b - A·x 与分量向后误差 η；
3. η ≤ 容差则接受；否则用同一分解解校正方程 A·dx = r，x ← x + dx；
4. η 停滞（连续两次改善不足 stall_ratio）、发散、出现非有限值或迭代
   耗尽时升级到下一精度级，绝不重复报收敛；
5. 升至最高级仍不达标则返回 not_converged；若高精度秩判定确认矩阵
   奇异则返回 singular。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mpmath as mp
import numpy as np

from config.settings import SolverSettings

from . import evidence
from .condition import ConditionReport, estimate_condition
from .diagnostics import DecisionJournal, matrix_summary, new_request_id
from .factor import SingularFactorError, build_factor
from .inputs import MatrixInput
from .precision import PrecisionTier, build_ladder
from .residual import PreparedSystem, compute_residual

CONVERGED = "converged"
NOT_CONVERGED = "not_converged"
SINGULAR = "singular"
DIVERGED = "diverged"

_STALL_LIMIT = 2  # 连续停滞迭代数达到该值即升级
_DIVERGE_LIMIT = 2  # 连续发散迭代数达到该值即升级
_DIVERGE_FACTOR = 10.0  # η 单次放大超过该倍数记为一次发散


@dataclass
class ColumnReport:
    """单个右端列的完整求解报告。"""

    index: int
    status: str
    tier_used: str | None
    iterations: int
    trace: list[evidence.IterationRecord]
    eta_componentwise: float | None
    eta_normwise: float | None
    forward_error_bound: float | None
    solution: list[float] | None
    message: str


@dataclass
class SolveReport:
    """整个系统（全部右端列）的求解报告。"""

    request_id: str
    status: str
    condition: ConditionReport
    accuracy_note: str
    columns: list[ColumnReport]
    journal: list[dict]
    matrix_summary: dict


@dataclass
class _TierOutcome:
    status: str  # converged | stalled | exhausted | diverged | nonfinite
    x_native: object
    records: list[evidence.IterationRecord]
    eta: tuple[float, float] | None


def _finite_or_none(v: float) -> float | None:
    return v if math.isfinite(v) else None


def _x_to_native(x64: np.ndarray, kind: str) -> object:
    if kind == "float64":
        return np.asarray(x64, dtype=np.float64)
    if kind == "longdouble":
        return np.asarray(x64, dtype=np.longdouble)
    return [mp.mpf(float(v)) for v in x64]


def _x_to_float64(x_native: object) -> np.ndarray:
    if isinstance(x_native, list):
        return np.array([float(v) for v in x_native], dtype=np.float64)
    return np.asarray(x_native, dtype=np.float64)


def _add_correction(x_native: object, dx_native: object, kind: str) -> object:
    if kind == "mpmath":
        return [a + b for a, b in zip(x_native, dx_native)]  # type: ignore[union-attr]
    return x_native + np.asarray(dx_native, dtype=x_native.dtype)  # type: ignore[union-attr]


def _initial_x(fact: object, prep: PreparedSystem, col: int, tier: PrecisionTier) -> object:
    """当前精度级的首次直接求解，结果提升到该级残差精度存放。"""
    _, b_cols = prep.operands(tier.residual_kind)
    dx = fact.solve(b_cols[col])  # type: ignore[attr-defined]
    if tier.residual_kind == "mpmath":
        return list(dx)
    dtype = np.longdouble if tier.residual_kind == "longdouble" else np.float64
    return np.asarray(dx, dtype=dtype)


def _get_factor(cache: dict, tier: PrecisionTier, prep: PreparedSystem) -> object:
    """分解只依赖矩阵，按阶段缓存并在各右端列间共享。"""
    if tier.name not in cache:
        try:
            cache[tier.name] = build_factor(tier, prep.A, prep.A64)
        except SingularFactorError as exc:
            cache[tier.name] = exc
    cached = cache[tier.name]
    if isinstance(cached, SingularFactorError):
        raise cached
    return cached


def _run_tier(
    tier: PrecisionTier,
    fact: object,
    prep: PreparedSystem,
    col: int,
    x_native: object,
    settings: SolverSettings,
    journal: DecisionJournal,
) -> _TierOutcome:
    """在一个精度级上执行精化迭代，直到收敛、停滞、发散或耗尽。"""
    records: list[evidence.IterationRecord] = []
    A64, b64_cols = prep.operands("float64")
    b64 = b64_cols[col]
    prev_eta: float | None = None
    flat_steps = 0
    div_steps = 0
    eta = (math.inf, math.inf)
    for iteration in range(tier.max_iter + 1):
        residual = compute_residual(prep, col, x_native, tier.residual_kind)
        x64 = _x_to_float64(x_native)
        eta = evidence.backward_errors(A64, x64, b64, residual.as_float64)
        record = evidence.IterationRecord(
            tier.name, iteration, _finite_or_none(eta[0]), _finite_or_none(eta[1])
        )
        records.append(record)
        journal.record(
            "iteration", column=col, tier=tier.name, iteration=iteration,
            eta_comp=record.eta_componentwise, eta_norm=record.eta_normwise,
        )
        if not math.isfinite(eta[0]) or not np.all(np.isfinite(x64)):
            return _TierOutcome("nonfinite", x_native, records, None)
        if eta[0] <= settings.tolerance:
            return _TierOutcome(CONVERGED, x_native, records, eta)
        if iteration >= tier.max_iter:
            break
        if prev_eta is not None:
            flat_steps = flat_steps + 1 if eta[0] >= prev_eta * settings.stall_ratio else 0
            div_steps = div_steps + 1 if eta[0] > prev_eta * _DIVERGE_FACTOR else 0
            if flat_steps >= _STALL_LIMIT:
                return _TierOutcome("stalled", x_native, records, eta)
            if div_steps >= _DIVERGE_LIMIT:
                return _TierOutcome(DIVERGED, x_native, records, eta)
        prev_eta = eta[0]
        dx = fact.solve(residual.native)  # type: ignore[attr-defined]
        dx64 = _x_to_float64(list(dx) if isinstance(dx, list) else dx)
        record.step_norm = float(np.max(np.abs(dx64))) if dx64.size else 0.0
        if not np.all(np.isfinite(dx64)):
            return _TierOutcome("nonfinite", x_native, records, None)
        x_native = _add_correction(x_native, dx, tier.residual_kind)
    return _TierOutcome("exhausted", x_native, records, eta)


def _finish_column(
    col: int,
    status: str,
    tier_used: str | None,
    trace: list[evidence.IterationRecord],
    last_eta: tuple[float, float] | None,
    cond: ConditionReport,
    notes: list[str],
    x64: np.ndarray | None = None,
) -> ColumnReport:
    eta_c = None if last_eta is None else last_eta[0]
    eta_n = None if last_eta is None else last_eta[1]
    fwd = None
    if eta_c is not None and cond.kappa is not None and math.isfinite(cond.kappa):
        fwd = evidence.forward_error_bound(cond.kappa, eta_c)
    return ColumnReport(
        index=col,
        status=status,
        tier_used=tier_used,
        iterations=len(trace),
        trace=trace,
        eta_componentwise=eta_c,
        eta_normwise=eta_n,
        forward_error_bound=fwd,
        solution=None if x64 is None else [float(v) for v in x64],
        message="；".join(notes),
    )


def _converged_note(tier_name: str, outcome: _TierOutcome, tolerance: float) -> str:
    return (
        f"{tier_name} 阶段第 {outcome.records[-1].iteration} 次校正后 "
        f"η={outcome.eta[0]:.2e} ≤ 容差 {tolerance:.1e}，接受"  # type: ignore[index]
    )


def _escalation_note(tier_name: str, outcome: _TierOutcome) -> tuple[str, str]:
    """返回 (升级原因, 人可读说明)。"""
    reason = {"stalled": "η 停滞", "exhausted": "迭代次数耗尽", DIVERGED: "η 发散"}.get(
        outcome.status, outcome.status
    )
    eta_text = "不可得" if outcome.eta is None else f"η={outcome.eta[0]:.2e}"
    return reason, f"{tier_name} 阶段{reason}（{eta_text}），升级精度"


def _solve_column(
    prep: PreparedSystem,
    col: int,
    ladder: tuple[PrecisionTier, ...],
    settings: SolverSettings,
    journal: DecisionJournal,
    cond: ConditionReport,
    factor_cache: dict,
) -> ColumnReport:
    trace: list[evidence.IterationRecord] = []
    notes: list[str] = []
    x_native: object | None = None
    x64: np.ndarray | None = None
    last_eta: tuple[float, float] | None = None
    tier_used: str | None = None

    for tier in ladder:
        tier_used = tier.name
        try:
            fact = _get_factor(factor_cache, tier, prep)
        except SingularFactorError as exc:
            journal.record("factor_singular", column=col, tier=tier.name, detail=str(exc))
            notes.append(f"{tier.name} 分解遇到精确零主元：{exc}")
            return _finish_column(col, SINGULAR, tier_used, trace, last_eta, cond, notes)
        x_native = (
            _initial_x(fact, prep, col, tier)
            if x_native is None
            else _x_to_native(x64, tier.residual_kind)  # type: ignore[arg-type]
        )
        outcome = _run_tier(tier, fact, prep, col, x_native, settings, journal)
        trace.extend(outcome.records)
        if outcome.status == CONVERGED:
            x64 = _x_to_float64(outcome.x_native)
            last_eta = outcome.eta
            notes.append(_converged_note(tier.name, outcome, settings.tolerance))
            journal.record(
                "converged", column=col, tier=tier.name,
                eta_comp=outcome.eta[0], iterations=len(trace),  # type: ignore[index]
            )
            return _finish_column(col, CONVERGED, tier_used, trace, last_eta, cond, notes, x64)
        if outcome.status == "nonfinite":
            notes.append(f"{tier.name} 阶段解出现非有限值，放弃当前迭代并升级精度")
            journal.record("escalate", column=col, from_tier=tier.name, reason="nonfinite")
            x_native = None  # 放弃被污染的迭代，下一级重新直接求解
            continue
        if outcome.eta is not None:
            last_eta = outcome.eta
            x64 = _x_to_float64(outcome.x_native)
        reason, note = _escalation_note(tier.name, outcome)
        notes.append(note)
        journal.record(
            "escalate", column=col, from_tier=tier.name, reason=reason,
            eta=None if outcome.eta is None else outcome.eta[0],
        )

    if cond.rank_deficient:
        notes.append(f"高精度秩判定 rank={cond.rank} < 矩阵阶数，矩阵奇异")
        journal.record("singular", column=col, rank=cond.rank)
        return _finish_column(col, SINGULAR, tier_used, trace, last_eta, cond, notes)
    notes.append("已升至最高精度仍无法达到容差，判定未达标（不重复报收敛）")
    journal.record(
        "not_converged", column=col, eta=None if last_eta is None else last_eta[0]
    )
    return _finish_column(col, NOT_CONVERGED, tier_used, trace, last_eta, cond, notes)


def _overall_status(columns: list[ColumnReport]) -> str:
    statuses = {c.status for c in columns}
    if statuses == {CONVERGED}:
        return CONVERGED
    if CONVERGED in statuses:
        return "partial"
    if statuses == {SINGULAR}:
        return SINGULAR
    return "failed"


def solve_system(
    A: MatrixInput,
    B: MatrixInput,
    settings: SolverSettings,
    request_id: str | None = None,
    sensitive: bool = False,
) -> SolveReport:
    """求解 A·X = B，每个右端列独立精化、独立报告。"""
    request_id = request_id or new_request_id()
    journal = DecisionJournal(request_id, sensitive)
    prep = PreparedSystem(A, B, settings.mp_dps)
    cond = estimate_condition(A, prep.A64, settings.cond_threshold, settings.mp_dps)
    journal.record("condition", kappa=cond.kappa, method=cond.method, rank=cond.rank)
    summary = matrix_summary(A, B, cond.kappa, sensitive)
    note = evidence.accuracy_note(cond.kappa, cond.method, settings.tolerance)

    if cond.rank_deficient:
        journal.record("singular", rank=cond.rank, method=cond.method)
        columns = [
            ColumnReport(
                j, SINGULAR, None, 0, [], None, None, None, None,
                f"高精度秩判定 rank={cond.rank} < {A.n_rows}（{cond.method}），"
                "矩阵奇异，未尝试求解",
            )
            for j in range(B.n_cols)
        ]
        return SolveReport(request_id, SINGULAR, cond, note, columns, journal.entries, summary)

    ladder = build_ladder(settings)
    factor_cache: dict = {}
    columns = [
        _solve_column(prep, j, ladder, settings, journal, cond, factor_cache)
        for j in range(B.n_cols)
    ]
    status = _overall_status(columns)
    journal.record("final", status=status)
    return SolveReport(request_id, status, cond, note, columns, journal.entries, summary)
