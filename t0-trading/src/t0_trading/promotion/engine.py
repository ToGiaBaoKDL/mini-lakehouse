"""Pure promotion evaluation over prospectively arbitrated shadow evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import cast

from t0_trading.configuration import (
    BaselineEvaluationVersion,
    CandidateArbitrationVersion,
    PromotionGateVersion,
    PromotionTarget,
)
from t0_trading.numeric import rate, ratio
from t0_trading.promotion.model import (
    ArbitratedHoldoutEvaluation,
    ArbitratedSessionEvaluation,
    ArbitratedSessionReport,
    ArbitratedWalkForwardFold,
    ArbitratedWalkForwardReport,
    PromotionGateReport,
    PromotionReason,
    PromotionStatus,
    PromotionTargetResult,
)
from t0_trading.strategy.baselines import BaselineName


def _weighted_average(
    rows: Sequence[ArbitratedSessionEvaluation | ArbitratedHoldoutEvaluation],
) -> Decimal | None:
    eligible = sum(item.eligible_outcome_count for item in rows)
    if eligible == 0:
        return None
    return ratio(
        sum(
            (
                item.average_conditional_net_return_bps * item.eligible_outcome_count
                for item in rows
                if item.average_conditional_net_return_bps is not None
            ),
            Decimal(0),
        ),
        eligible,
        quantum=Decimal("0.0001"),
    )


def _combine(rows: Sequence[ArbitratedSessionEvaluation]) -> ArbitratedHoldoutEvaluation:
    keys = {(item.strategy, item.symbol, item.horizon_seconds) for item in rows}
    if not rows or len(keys) != 1:
        raise ValueError("arbitrated holdout rows must share one target")
    strategy, symbol, horizon = next(iter(keys))
    return ArbitratedHoldoutEvaluation(
        strategy=cast(BaselineName, strategy),
        symbol=symbol,
        horizon_seconds=horizon,
        selected_count=sum(item.selected_count for item in rows),
        eligible_outcome_count=sum(item.eligible_outcome_count for item in rows),
        positive_net_count=sum(item.positive_net_count for item in rows),
        average_conditional_net_return_bps=_weighted_average(rows),
    )


def evaluate_arbitrated_walk_forward(
    sessions: Mapping[date, ArbitratedSessionReport],
    policy: BaselineEvaluationVersion,
    arbitration_policy: CandidateArbitrationVersion,
) -> ArbitratedWalkForwardReport:
    """Build expanding-window folds from immutable selected-population evidence."""
    dates = tuple(sorted(sessions))
    required = policy.development_sessions + policy.purge_sessions + policy.holdout_sessions
    if len(dates) < required:
        raise ValueError(f"arbitrated walk-forward requires at least {required} sessions")
    reports = tuple(sessions[value] for value in dates)
    if any(
        item.trade_date != value
        or not policy.contains(value)
        or not arbitration_policy.contains(value)
        or item.arbitration_version != arbitration_policy.version
        or item.arbitration_configuration_sha256 != arbitration_policy.sha256
        for value, item in zip(dates, reports, strict=True)
    ):
        raise ValueError("arbitrated sessions do not match prospective policies")
    lineages = {
        (
            item.baseline_version,
            item.feature_configuration_sha256,
            item.context_configuration_sha256,
            item.outcome_configuration_sha256,
            item.cost_policy_sha256,
        )
        for item in reports
    }
    if len(lineages) != 1:
        raise ValueError("arbitrated sessions must share complete research lineage")
    keys = tuple(
        (item.strategy, item.symbol, item.horizon_seconds) for item in reports[0].evaluations
    )
    if any(
        tuple((row.strategy, row.symbol, row.horizon_seconds) for row in report.evaluations) != keys
        for report in reports
    ):
        raise ValueError("arbitrated sessions must share one ordered target matrix")
    keyed = {
        value: {
            (item.strategy, item.symbol, item.horizon_seconds): item
            for item in sessions[value].evaluations
        }
        for value in dates
    }
    folds: list[ArbitratedWalkForwardFold] = []
    holdout_start = policy.development_sessions + policy.purge_sessions
    while holdout_start + policy.holdout_sessions <= len(dates):
        development_end = holdout_start - policy.purge_sessions
        development = dates[:development_end]
        purged = dates[development_end:holdout_start]
        holdout = dates[holdout_start : holdout_start + policy.holdout_sessions]
        folds.append(
            ArbitratedWalkForwardFold(
                fold=len(folds) + 1,
                development_dates=development,
                purged_dates=purged,
                holdout_dates=holdout,
                evaluations=tuple(
                    _combine(tuple(keyed[value][key] for value in holdout)) for key in keys
                ),
            )
        )
        holdout_start += policy.holdout_sessions
    last_holdout = folds[-1].holdout_dates[-1]
    return ArbitratedWalkForwardReport(
        evaluation_version=policy.version,
        evaluation_configuration_sha256=policy.sha256,
        arbitration_version=arbitration_policy.version,
        arbitration_configuration_sha256=arbitration_policy.sha256,
        session_sha256s=tuple(item.sha256 for item in reports),
        folds=tuple(folds),
        pending_dates=tuple(value for value in dates if value > last_holdout),
    )


def _target_result(
    target: PromotionTarget,
    report: ArbitratedWalkForwardReport,
    policy: PromotionGateVersion,
) -> PromotionTargetResult:
    rows = tuple(
        next(
            item
            for item in fold.evaluations
            if (item.strategy, item.symbol, item.horizon_seconds)
            == (target.strategy, target.symbol, target.horizon_seconds)
        )
        for fold in report.folds
    )
    selected = sum(item.selected_count for item in rows)
    eligible = sum(item.eligible_outcome_count for item in rows)
    positive = sum(item.positive_net_count for item in rows)
    average = _weighted_average(rows)
    fold_returns = tuple(
        item.average_conditional_net_return_bps
        for item in rows
        if item.average_conditional_net_return_bps is not None
    )
    worst = min(fold_returns) if len(fold_returns) == len(rows) else None
    pending: list[PromotionReason] = []
    failed: list[PromotionReason] = []
    if selected < policy.minimum_selected_count:
        pending.append("INSUFFICIENT_SELECTIONS")
    else:
        if rate(eligible, selected) < policy.minimum_outcome_coverage_rate:
            failed.append("OUTCOME_COVERAGE")
        if worst is None:
            pending.append("MISSING_FOLD_OUTCOMES")
        if eligible and rate(positive, eligible) < policy.minimum_positive_net_rate:
            failed.append("POSITIVE_NET_RATE")
        if average is not None and average < policy.minimum_average_net_return_bps:
            failed.append("AVERAGE_NET_RETURN")
        if worst is not None and worst < policy.minimum_worst_fold_net_return_bps:
            failed.append("WORST_FOLD_NET_RETURN")
    status: PromotionStatus = "FAIL" if failed else "PENDING" if pending else "PASS"
    return PromotionTargetResult(
        strategy=rows[0].strategy,
        symbol=rows[0].symbol,
        horizon_seconds=target.horizon_seconds,
        status=status,
        reasons=tuple((*pending, *failed)),
        selected_count=selected,
        eligible_outcome_count=eligible,
        positive_net_count=positive,
        outcome_coverage_rate=rate(eligible, selected),
        positive_net_rate=rate(positive, eligible),
        average_net_return_bps=average,
        worst_fold_net_return_bps=worst,
    )


def evaluate_promotion_gate(
    sessions: Mapping[date, ArbitratedSessionReport],
    evaluation_policy: BaselineEvaluationVersion,
    gate_policy: PromotionGateVersion,
    arbitration_policy: CandidateArbitrationVersion,
) -> PromotionGateReport:
    """Evaluate shadow-to-paper readiness over actual selected candidates only."""
    if evaluation_policy.tier != "PROMOTION":
        raise ValueError("promotion gate requires a PROMOTION evaluation policy")
    if (
        gate_policy.evaluation_version != evaluation_policy.version
        or gate_policy.arbitration_version != arbitration_policy.version
    ):
        raise ValueError("promotion gate policy lineage is inconsistent")
    required = (
        evaluation_policy.development_sessions
        + evaluation_policy.purge_sessions
        + evaluation_policy.holdout_sessions
    )
    if len(sessions) < required:
        targets = tuple(
            PromotionTargetResult(
                strategy=cast(BaselineName, item.strategy),
                symbol=item.symbol,
                horizon_seconds=item.horizon_seconds,
                status="PENDING",
                reasons=("INSUFFICIENT_SELECTIONS",),
                selected_count=0,
                eligible_outcome_count=0,
                positive_net_count=0,
                outcome_coverage_rate=Decimal(0),
                positive_net_rate=Decimal(0),
                average_net_return_bps=None,
                worst_fold_net_return_bps=None,
            )
            for item in gate_policy.targets
        )
        return PromotionGateReport(
            gate_version=gate_policy.version,
            gate_configuration_sha256=gate_policy.sha256,
            evaluation_version=evaluation_policy.version,
            evaluation_configuration_sha256=evaluation_policy.sha256,
            arbitration_version=arbitration_policy.version,
            arbitration_configuration_sha256=arbitration_policy.sha256,
            required_session_count=required,
            observed_session_count=len(sessions),
            holdout_dates=(),
            capture_gap_count=0,
            shadow_audit_sha256s=(),
            status="PENDING",
            reasons=("INSUFFICIENT_SESSIONS",),
            targets=targets,
        )
    walk_forward = evaluate_arbitrated_walk_forward(sessions, evaluation_policy, arbitration_policy)
    holdout_dates = tuple(
        sorted(value for fold in walk_forward.folds for value in fold.holdout_dates)
    )
    holdout_sessions = tuple(sessions[value] for value in holdout_dates)
    gaps = sum(item.capture_gap_count for item in holdout_sessions)
    reasons: tuple[PromotionReason, ...] = (
        ("CAPTURE_GAPS",) if gaps > gate_policy.maximum_capture_gap_count else ()
    )
    targets = tuple(
        _target_result(target, walk_forward, gate_policy) for target in gate_policy.targets
    )
    status: PromotionStatus = (
        "FAIL"
        if reasons or any(item.status == "FAIL" for item in targets)
        else "PENDING"
        if any(item.status == "PENDING" for item in targets)
        else "PASS"
    )
    return PromotionGateReport(
        gate_version=gate_policy.version,
        gate_configuration_sha256=gate_policy.sha256,
        evaluation_version=evaluation_policy.version,
        evaluation_configuration_sha256=evaluation_policy.sha256,
        arbitration_version=arbitration_policy.version,
        arbitration_configuration_sha256=arbitration_policy.sha256,
        required_session_count=required,
        observed_session_count=len(sessions),
        holdout_dates=holdout_dates,
        capture_gap_count=gaps,
        walk_forward_sha256=walk_forward.sha256,
        shadow_audit_sha256s=tuple(item.shadow_audit_sha256 for item in holdout_sessions),
        status=status,
        reasons=reasons,
        targets=targets,
    )
