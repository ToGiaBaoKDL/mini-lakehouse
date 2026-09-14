"""Leakage-safe gross outcome summary for deterministic strategy scores."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.configuration import OutcomeVersion, StrategyVersion
from t0_trading.numeric import BPS_QUANTUM, rate, ratio
from t0_trading.outcomes import OutcomeLabel
from t0_trading.strategy.model import STRATEGY_NAMES, StrategyName, StrategyScore


class StrategyHorizonEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: StrategyName
    horizon_seconds: int = Field(ge=1)
    score_count: int = Field(ge=0)
    directed_score_count: int = Field(ge=0)
    eligible_outcome_count: int = Field(ge=0)
    positive_outcome_count: int = Field(ge=0)
    directed_rate: Decimal = Field(ge=0, le=1)
    outcome_coverage_rate: Decimal = Field(ge=0, le=1)
    positive_outcome_rate: Decimal = Field(ge=0, le=1)
    average_gross_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_counts(self) -> StrategyHorizonEvaluation:
        if not (
            self.positive_outcome_count
            <= self.eligible_outcome_count
            <= self.directed_score_count
            <= self.score_count
        ) or self.directed_rate != rate(self.directed_score_count, self.score_count):
            raise ValueError("strategy evaluation counts are inconsistent")
        if self.outcome_coverage_rate != rate(
            self.eligible_outcome_count, self.directed_score_count
        ) or self.positive_outcome_rate != rate(
            self.positive_outcome_count, self.eligible_outcome_count
        ):
            raise ValueError("strategy evaluation rates are inconsistent")
        if (self.eligible_outcome_count == 0) != (self.average_gross_return_bps is None):
            raise ValueError("average gross return must match eligible outcome coverage")
        return self


class StrategyEvaluationReport(BaseModel):
    """Stable one-session score coverage and gross markout report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trade_date: date
    strategy_version: str
    strategy_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_version: str
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score_count: int = Field(ge=0)
    directed_score_count: int = Field(ge=0)
    evaluations: tuple[StrategyHorizonEvaluation, ...]

    @model_validator(mode="after")
    def validate_report(self) -> StrategyEvaluationReport:
        if self.directed_score_count > self.score_count:
            raise ValueError("directed score count exceeds total score count")
        keys = {(item.strategy, item.horizon_seconds) for item in self.evaluations}
        if len(keys) != len(self.evaluations):
            raise ValueError("strategy evaluation keys must be unique")
        grouped = {
            strategy: tuple(item for item in self.evaluations if item.strategy == strategy)
            for strategy in STRATEGY_NAMES
        }
        horizon_sets = {
            tuple(item.horizon_seconds for item in evaluations) for evaluations in grouped.values()
        }
        if (
            not self.evaluations
            or any(not evaluations for evaluations in grouped.values())
            or len(horizon_sets) != 1
            or any(
                len({item.score_count for item in evaluations}) != 1
                or len({item.directed_score_count for item in evaluations}) != 1
                for evaluations in grouped.values()
            )
            or sum(evaluations[0].score_count for evaluations in grouped.values())
            != self.score_count
            or sum(evaluations[0].directed_score_count for evaluations in grouped.values())
            != self.directed_score_count
        ):
            raise ValueError("strategy evaluation report does not reconcile")
        return self


def average_gross_return(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return ratio(sum(values, Decimal(0)), len(values), quantum=BPS_QUANTUM)


def match_directed_outcomes(
    scores: Sequence[StrategyScore],
    labels: Sequence[OutcomeLabel],
    strategy_policy: StrategyVersion,
    outcome_policy: OutcomeVersion,
    *,
    trade_date: date,
) -> dict[int, tuple[tuple[StrategyScore, OutcomeLabel], ...]]:
    """Validate lineage and match directed scores to every configured horizon."""
    if not strategy_policy.contains(trade_date) or not outcome_policy.contains(trade_date):
        raise ValueError("evaluation policies are not effective for the trade date")
    score_keys = {(score.strategy, score.feature_snapshot_sha256) for score in scores}
    if len(score_keys) != len(scores):
        raise ValueError("strategy scores must be unique")
    if any(
        score.trade_date != trade_date
        or score.strategy_version != strategy_policy.version
        or score.strategy_configuration_sha256 != strategy_policy.sha256
        for score in scores
    ):
        raise ValueError("strategy score lineage is inconsistent")

    label_keys = [
        (
            label.outcome_configuration_sha256,
            label.feature_snapshot_sha256,
            label.action,
            label.horizon_seconds,
        )
        for label in labels
    ]
    if len(set(label_keys)) != len(label_keys):
        raise ValueError("outcome labels must be unique for evaluation")
    if any(
        label.trade_date != trade_date
        or label.outcome_version != outcome_policy.version
        or label.outcome_configuration_sha256 != outcome_policy.sha256
        for label in labels
    ):
        raise ValueError("outcome label policy lineage is inconsistent")
    labels_by_key = dict(zip(label_keys, labels, strict=True))

    matched_by_horizon: dict[int, tuple[tuple[StrategyScore, OutcomeLabel], ...]] = {}
    directed = tuple(score for score in scores if score.direction is not None)
    for horizon in outcome_policy.horizons_seconds:
        matched: list[tuple[StrategyScore, OutcomeLabel]] = []
        for score in directed:
            direction = score.direction
            if direction is None:
                raise ValueError("directed strategy score has no direction")
            key = (
                outcome_policy.sha256,
                score.feature_snapshot_sha256,
                direction,
                horizon,
            )
            label = labels_by_key.get(key)
            if label is None:
                raise ValueError("outcomes do not cover every directed strategy score")
            if (
                label.symbol != score.symbol
                or label.decision_at != score.decision_at
                or label.feature_version != score.feature_version
                or label.feature_configuration_sha256 != score.feature_configuration_sha256
            ):
                raise ValueError("strategy outcome lineage is inconsistent")
            matched.append((score, label))
        matched_by_horizon[horizon] = tuple(matched)
    return matched_by_horizon


def evaluate_scores(
    scores: Sequence[StrategyScore],
    labels: Sequence[OutcomeLabel],
    strategy_policy: StrategyVersion,
    outcome_policy: OutcomeVersion,
    *,
    trade_date: date,
) -> StrategyEvaluationReport:
    """Summarize exact conditional outcomes for one session of strategy scores."""
    matched_by_horizon = match_directed_outcomes(
        scores,
        labels,
        strategy_policy,
        outcome_policy,
        trade_date=trade_date,
    )

    evaluations: list[StrategyHorizonEvaluation] = []
    for strategy in STRATEGY_NAMES:
        selected_scores = tuple(score for score in scores if score.strategy == strategy)
        directed_count = sum(score.direction is not None for score in selected_scores)
        for horizon in outcome_policy.horizons_seconds:
            matched = tuple(
                label for score, label in matched_by_horizon[horizon] if score.strategy == strategy
            )
            eligible_returns = tuple(
                label.gross_return_bps
                for label in matched
                if label.is_eligible and label.gross_return_bps is not None
            )
            positive_count = sum(value > 0 for value in eligible_returns)
            evaluations.append(
                StrategyHorizonEvaluation(
                    strategy=strategy,
                    horizon_seconds=horizon,
                    score_count=len(selected_scores),
                    directed_score_count=directed_count,
                    eligible_outcome_count=len(eligible_returns),
                    positive_outcome_count=positive_count,
                    directed_rate=rate(directed_count, len(selected_scores)),
                    outcome_coverage_rate=rate(len(eligible_returns), directed_count),
                    positive_outcome_rate=rate(positive_count, len(eligible_returns)),
                    average_gross_return_bps=average_gross_return(eligible_returns),
                )
            )

    return StrategyEvaluationReport(
        trade_date=trade_date,
        strategy_version=strategy_policy.version,
        strategy_configuration_sha256=strategy_policy.sha256,
        outcome_version=outcome_policy.version,
        outcome_configuration_sha256=outcome_policy.sha256,
        score_count=len(scores),
        directed_score_count=sum(score.direction is not None for score in scores),
        evaluations=tuple(evaluations),
    )
