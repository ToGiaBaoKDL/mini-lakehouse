"""Build daily promotion evidence from the exact arbitrated shadow population."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import cast

from t0_trading.arbitration import (
    CandidateArbitration,
    ShadowArbitrationAuditReport,
    require_selected_candidate,
)
from t0_trading.configuration import PromotionGateVersion, TradingConfiguration
from t0_trading.context.regime import context_identity
from t0_trading.controls import CostPolicy, conditional_net_return_bps
from t0_trading.identity import canonical_json, sha256
from t0_trading.numeric import ratio
from t0_trading.outcomes import OutcomeLabel
from t0_trading.promotion.model import (
    ArbitratedSessionEvaluation,
    ArbitratedSessionReport,
    ResearchLineage,
)
from t0_trading.strategy.baselines import BASELINE_VERSION, BaselineCandidate, BaselineName


def resolve_research_lineage(
    configuration: TradingConfiguration, trade_date: date, costs: CostPolicy
) -> ResearchLineage:
    """Resolve current assumptions without reading performance or a previous PASS."""
    arbitration = configuration.resolve_candidate_arbitration(trade_date)
    if arbitration is None or not costs.contains(trade_date):
        raise ValueError("research lineage requires effective arbitration and costs")
    return ResearchLineage(
        baseline_version=BASELINE_VERSION,
        arbitration_configuration_sha256=arbitration.sha256,
        feature_configuration_sha256=configuration.resolve(trade_date).sha256,
        context_configuration_sha256=context_identity(
            configuration.resolve_context(trade_date),
            configuration.resolve_regime(trade_date),
            configuration.resolve_breadth(trade_date),
        ),
        outcome_configuration_sha256=configuration.resolve_outcomes(trade_date).sha256,
        cost_policy_sha256=costs.assumptions_sha256,
    )


def _journal_sha256(records: Sequence[BaselineCandidate | CandidateArbitration]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(record.canonical_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def _average(values: Sequence[Decimal]) -> Decimal | None:
    return (
        ratio(sum(values, Decimal(0)), len(values), quantum=Decimal("0.0001")) if values else None
    )


def evaluate_arbitrated_session(
    candidates: Sequence[BaselineCandidate],
    arbitrations: Sequence[CandidateArbitration],
    outcomes: Sequence[OutcomeLabel],
    costs: CostPolicy,
    shadow_audit: ShadowArbitrationAuditReport,
    gate_policy: PromotionGateVersion,
    *,
    capture_evidence_sha256: str,
) -> ArbitratedSessionReport:
    """Evaluate only prospectively SELECTED candidates against exact outcome lineage."""
    if not candidates or len(candidates) != len(arbitrations):
        raise ValueError("arbitrated session requires a complete candidate decision matrix")
    trade_dates = {item.trade_date for item in candidates}
    if trade_dates != {shadow_audit.trade_date} or not gate_policy.contains(
        shadow_audit.trade_date
    ):
        raise ValueError("arbitrated session policy does not cover the shadow trade date")
    if not costs.contains(shadow_audit.trade_date):
        raise ValueError("arbitrated session cost policy is not effective")
    if (
        shadow_audit.arbitration_version != gate_policy.arbitration_version
        or shadow_audit.candidate_count != len(candidates)
        or shadow_audit.arbitration_count != len(arbitrations)
        or shadow_audit.candidate_sha256 != _journal_sha256(candidates)
        or shadow_audit.arbitration_sha256 != _journal_sha256(arbitrations)
        or shadow_audit.capture_evidence_sha256 != capture_evidence_sha256
    ):
        raise ValueError("arbitrated session does not match proven shadow journals")
    candidate_by_sha = {item.sha256: item for item in candidates}
    arbitration_by_candidate = {item.candidate_sha256: item for item in arbitrations}
    if len(candidate_by_sha) != len(candidates) or set(candidate_by_sha) != set(
        arbitration_by_candidate
    ):
        raise ValueError("arbitrated session candidate/arbitration matrix is incomplete")
    selected: list[tuple[BaselineCandidate, CandidateArbitration]] = []
    for candidate in candidates:
        arbitration = arbitration_by_candidate[candidate.sha256]
        if arbitration.status == "SELECTED":
            require_selected_candidate(candidate, arbitration)
            selected.append((candidate, arbitration))
    buy_outcomes = [item for item in outcomes if item.action == "BUY"]
    if any(item.stream_session_id not in shadow_audit.stream_session_ids for item in buy_outcomes):
        raise ValueError("arbitrated session outcomes use unproven capture sessions")
    outcome_by_key = {
        (item.feature_snapshot_sha256, item.horizon_seconds): item for item in buy_outcomes
    }
    if len(outcome_by_key) != len(buy_outcomes):
        raise ValueError("arbitrated session outcomes must be unique")
    required_keys = {
        (candidate.feature_snapshot_sha256, arbitration.horizon_seconds)
        for candidate, arbitration in selected
    }
    if not required_keys.issubset(outcome_by_key):
        raise ValueError("arbitrated session outcomes do not cover every selection")
    outcome_lineages = {
        (item.feature_configuration_sha256, item.outcome_configuration_sha256)
        for item in buy_outcomes
    }
    context_lineages = {
        item.context_configuration_sha256
        for item in candidates
        if item.context_configuration_sha256
    }
    if len(outcome_lineages) != 1 or len(context_lineages) != 1:
        raise ValueError("arbitrated session research lineage must be homogeneous")
    feature_sha, outcome_sha = next(iter(outcome_lineages))
    evaluations: list[ArbitratedSessionEvaluation] = []
    for target in gate_policy.targets:
        target_selected = [
            (candidate, arbitration)
            for candidate, arbitration in selected
            if (candidate.strategy, candidate.symbol, arbitration.horizon_seconds)
            == (target.strategy, target.symbol, target.horizon_seconds)
        ]
        net_returns: list[Decimal] = []
        for candidate, arbitration in target_selected:
            label = outcome_by_key[(candidate.feature_snapshot_sha256, arbitration.horizon_seconds)]
            if (
                label.trade_date != candidate.trade_date
                or label.symbol != candidate.symbol
                or label.decision_at != candidate.decision_at
                or label.feature_configuration_sha256 != feature_sha
            ):
                raise ValueError("selected outcome does not match candidate lineage")
            if label.is_eligible:
                net_returns.append(conditional_net_return_bps(label, costs))
        evaluations.append(
            ArbitratedSessionEvaluation(
                strategy=cast(BaselineName, target.strategy),
                symbol=target.symbol,
                horizon_seconds=target.horizon_seconds,
                selected_count=len(target_selected),
                eligible_outcome_count=len(net_returns),
                positive_net_count=sum(value > 0 for value in net_returns),
                average_conditional_net_return_bps=_average(net_returns),
            )
        )
    return ArbitratedSessionReport(
        trade_date=shadow_audit.trade_date,
        baseline_version=shadow_audit.baseline_version,
        arbitration_version=shadow_audit.arbitration_version,
        arbitration_configuration_sha256=shadow_audit.arbitration_configuration_sha256,
        feature_configuration_sha256=feature_sha,
        context_configuration_sha256=next(iter(context_lineages)),
        outcome_configuration_sha256=outcome_sha,
        cost_policy_sha256=costs.sha256,
        cost_assumptions_sha256=costs.assumptions_sha256,
        capture_evidence_sha256=capture_evidence_sha256,
        shadow_audit_sha256=sha256(canonical_json(shadow_audit.model_dump(mode="json"))),
        capture_gap_count=shadow_audit.gap_count,
        evaluations=tuple(evaluations),
    )
