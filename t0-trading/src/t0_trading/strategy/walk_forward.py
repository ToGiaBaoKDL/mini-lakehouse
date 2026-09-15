"""Purged walk-forward evaluation of out-of-sample score-strength buckets."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.configuration import (
    STRATEGY_NAMES,
    OutcomeVersion,
    StrategyEvaluationVersion,
    StrategyName,
    StrategyVersion,
)
from t0_trading.identity import canonical_json, sha256
from t0_trading.numeric import rate
from t0_trading.outcomes import Action, OutcomeLabel
from t0_trading.strategy.evaluation import average_gross_return, match_directed_outcomes
from t0_trading.strategy.model import StrategyScore

_Session = tuple[Sequence[StrategyScore], Sequence[OutcomeLabel]]
_DIRECTIONS: tuple[Action, ...] = ("BUY", "SELL")


class ScoreBucketEvaluation(BaseModel):
    """One out-of-sample result for a training-derived score-strength bucket."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: StrategyName
    direction: Action
    horizon_seconds: int = Field(ge=1)
    bucket: int = Field(ge=1)
    lower_strength_exclusive: Decimal | None = Field(default=None, ge=0, le=1)
    upper_strength_inclusive: Decimal | None = Field(default=None, ge=0, le=1)
    score_count: int = Field(ge=0)
    eligible_outcome_count: int = Field(ge=0)
    positive_outcome_count: int = Field(ge=0)
    outcome_coverage_rate: Decimal = Field(ge=0, le=1)
    positive_outcome_rate: Decimal = Field(ge=0, le=1)
    average_gross_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_bucket(self) -> ScoreBucketEvaluation:
        if (
            self.lower_strength_exclusive is not None
            and self.upper_strength_inclusive is not None
            and self.lower_strength_exclusive > self.upper_strength_inclusive
        ):
            raise ValueError("score bucket boundaries are inconsistent")
        if not self.positive_outcome_count <= self.eligible_outcome_count <= self.score_count:
            raise ValueError("score bucket counts are inconsistent")
        if self.outcome_coverage_rate != rate(self.eligible_outcome_count, self.score_count) or (
            self.positive_outcome_rate
            != rate(self.positive_outcome_count, self.eligible_outcome_count)
        ):
            raise ValueError("score bucket rates are inconsistent")
        if (self.eligible_outcome_count == 0) != (self.average_gross_return_bps is None):
            raise ValueError("score bucket return must match eligible outcome coverage")
        return self


class WalkForwardFold(BaseModel):
    """One expanding-window fold with an explicit pre-validation purge gap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fold: int = Field(ge=1)
    training_dates: tuple[date, ...] = Field(min_length=1)
    purged_dates: tuple[date, ...] = Field(min_length=1)
    validation_dates: tuple[date, ...] = Field(min_length=1)
    evaluations: tuple[ScoreBucketEvaluation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fold(self) -> WalkForwardFold:
        groups = (self.training_dates, self.purged_dates, self.validation_dates)
        if any(tuple(sorted(set(values))) != values for values in groups):
            raise ValueError("walk-forward dates must be unique and ascending")
        if (
            not self.training_dates[-1]
            < self.purged_dates[0]
            <= self.purged_dates[-1]
            < self.validation_dates[0]
        ):
            raise ValueError("walk-forward date groups must be ordered and disjoint")
        keys = {
            (item.strategy, item.direction, item.horizon_seconds, item.bucket)
            for item in self.evaluations
        }
        if len(keys) != len(self.evaluations):
            raise ValueError("walk-forward evaluation keys must be unique")
        return self


class WalkForwardReport(BaseModel):
    """Stable multi-session out-of-sample strategy evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    evaluation_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    evaluation_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    strategy_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    feature_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score_bucket_count: int = Field(ge=2)
    minimum_training_sessions: int = Field(ge=2)
    validation_sessions: int = Field(ge=1)
    purge_sessions: int = Field(ge=1)
    horizons_seconds: tuple[int, ...] = Field(min_length=1)
    folds: tuple[WalkForwardFold, ...] = Field(min_length=1)
    pending_dates: tuple[date, ...]

    @model_validator(mode="after")
    def validate_report(self) -> WalkForwardReport:
        if tuple(sorted(set(self.horizons_seconds))) != self.horizons_seconds:
            raise ValueError("walk-forward horizons must be unique and ascending")
        if tuple(fold.fold for fold in self.folds) != tuple(range(1, len(self.folds) + 1)):
            raise ValueError("walk-forward fold numbers must be contiguous")
        expected = tuple(
            (strategy, direction, horizon, bucket)
            for strategy in STRATEGY_NAMES
            for direction in _DIRECTIONS
            for horizon in self.horizons_seconds
            for bucket in range(1, self.score_bucket_count + 1)
        )
        for fold in self.folds:
            if (
                len(fold.training_dates) < self.minimum_training_sessions
                or len(fold.purged_dates) != self.purge_sessions
                or len(fold.validation_dates) != self.validation_sessions
            ):
                raise ValueError("walk-forward fold sizes are inconsistent")
            observed = tuple(
                (item.strategy, item.direction, item.horizon_seconds, item.bucket)
                for item in fold.evaluations
            )
            if observed != expected:
                raise ValueError("walk-forward fold does not contain the complete bucket matrix")
            for strategy in STRATEGY_NAMES:
                for direction in _DIRECTIONS:
                    reference = tuple(
                        item
                        for item in fold.evaluations
                        if item.strategy == strategy
                        and item.direction == direction
                        and item.horizon_seconds == self.horizons_seconds[0]
                    )
                    if (
                        reference[0].lower_strength_exclusive is not None
                        or reference[-1].upper_strength_inclusive is not None
                        or any(
                            previous.upper_strength_inclusive != current.lower_strength_exclusive
                            for previous, current in pairwise(reference)
                        )
                    ):
                        raise ValueError("score bucket boundaries are not contiguous")
                    for bucket in range(1, self.score_bucket_count + 1):
                        values = {
                            (
                                item.lower_strength_exclusive,
                                item.upper_strength_inclusive,
                                item.score_count,
                            )
                            for item in fold.evaluations
                            if item.strategy == strategy
                            and item.direction == direction
                            and item.bucket == bucket
                        }
                        if len(values) != 1:
                            raise ValueError("score bucket assignment differs across horizons")
        if self.pending_dates and (
            tuple(sorted(set(self.pending_dates))) != self.pending_dates
            or self.pending_dates[0] <= self.folds[-1].validation_dates[-1]
        ):
            raise ValueError("pending walk-forward dates are inconsistent")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


def _quantile_boundaries(scores: Sequence[StrategyScore], bucket_count: int) -> tuple[Decimal, ...]:
    strengths = sorted(abs(score.signed_score) for score in scores if score.direction is not None)
    if not strengths:
        raise ValueError("every strategy direction requires training scores")
    return tuple(
        strengths[(quantile * len(strengths) + bucket_count - 1) // bucket_count - 1]
        for quantile in range(1, bucket_count)
    )


def _bucket_evaluations(
    matches: Sequence[tuple[StrategyScore, OutcomeLabel]],
    boundaries: tuple[Decimal, ...],
    *,
    strategy: StrategyName,
    direction: Action,
    horizon_seconds: int,
) -> tuple[ScoreBucketEvaluation, ...]:
    grouped: list[list[OutcomeLabel]] = [[] for _ in range(len(boundaries) + 1)]
    for score, label in matches:
        grouped[bisect_left(boundaries, abs(score.signed_score))].append(label)

    evaluations: list[ScoreBucketEvaluation] = []
    for index, labels in enumerate(grouped):
        returns = tuple(
            label.gross_return_bps
            for label in labels
            if label.is_eligible and label.gross_return_bps is not None
        )
        positive_count = sum(value > 0 for value in returns)
        evaluations.append(
            ScoreBucketEvaluation(
                strategy=strategy,
                direction=direction,
                horizon_seconds=horizon_seconds,
                bucket=index + 1,
                lower_strength_exclusive=boundaries[index - 1] if index else None,
                upper_strength_inclusive=(boundaries[index] if index < len(boundaries) else None),
                score_count=len(labels),
                eligible_outcome_count=len(returns),
                positive_outcome_count=positive_count,
                outcome_coverage_rate=rate(len(returns), len(labels)),
                positive_outcome_rate=rate(positive_count, len(returns)),
                average_gross_return_bps=average_gross_return(returns),
            )
        )
    return tuple(evaluations)


def evaluate_walk_forward(
    sessions: Mapping[date, _Session],
    strategy_policy: StrategyVersion,
    outcome_policy: OutcomeVersion,
    evaluation_policy: StrategyEvaluationVersion,
) -> WalkForwardReport:
    """Fit strength quantiles on training dates and evaluate later purged folds."""
    dates = tuple(sorted(sessions))
    required = (
        evaluation_policy.minimum_training_sessions
        + evaluation_policy.purge_sessions
        + evaluation_policy.validation_sessions
    )
    if len(dates) < required:
        raise ValueError(f"walk-forward evaluation requires at least {required} sessions")
    if any(
        not strategy_policy.contains(trade_date)
        or not outcome_policy.contains(trade_date)
        or not evaluation_policy.contains(trade_date)
        for trade_date in dates
    ):
        raise ValueError("walk-forward policies must cover every session")

    matches_by_date: dict[date, dict[int, tuple[tuple[StrategyScore, OutcomeLabel], ...]]] = {}
    all_scores: list[StrategyScore] = []
    for trade_date in dates:
        scores, labels = sessions[trade_date]
        matches_by_date[trade_date] = match_directed_outcomes(
            scores,
            labels,
            strategy_policy,
            outcome_policy,
            trade_date=trade_date,
        )
        all_scores.extend(scores)
    feature_lineages = {
        (score.feature_version, score.feature_configuration_sha256) for score in all_scores
    }
    if len(feature_lineages) != 1:
        raise ValueError("walk-forward scores must share one feature lineage")
    feature_version, feature_configuration_sha256 = next(iter(feature_lineages))

    folds: list[WalkForwardFold] = []
    validation_start = (
        evaluation_policy.minimum_training_sessions + evaluation_policy.purge_sessions
    )
    validation_size = evaluation_policy.validation_sessions
    while validation_start + validation_size <= len(dates):
        training_end = validation_start - evaluation_policy.purge_sessions
        training_dates = dates[:training_end]
        purged_dates = dates[training_end:validation_start]
        validation_dates = dates[validation_start : validation_start + validation_size]
        training_scores = tuple(
            score for trade_date in training_dates for score in sessions[trade_date][0]
        )
        boundaries = {
            (strategy, direction): _quantile_boundaries(
                tuple(
                    score
                    for score in training_scores
                    if score.strategy == strategy and score.direction == direction
                ),
                evaluation_policy.score_bucket_count,
            )
            for strategy in STRATEGY_NAMES
            for direction in _DIRECTIONS
        }

        evaluations: list[ScoreBucketEvaluation] = []
        for strategy in STRATEGY_NAMES:
            for direction in _DIRECTIONS:
                for horizon in outcome_policy.horizons_seconds:
                    matches = tuple(
                        pair
                        for trade_date in validation_dates
                        for pair in matches_by_date[trade_date][horizon]
                        if pair[0].strategy == strategy and pair[0].direction == direction
                    )
                    evaluations.extend(
                        _bucket_evaluations(
                            matches,
                            boundaries[strategy, direction],
                            strategy=strategy,
                            direction=direction,
                            horizon_seconds=horizon,
                        )
                    )
        folds.append(
            WalkForwardFold(
                fold=len(folds) + 1,
                training_dates=training_dates,
                purged_dates=purged_dates,
                validation_dates=validation_dates,
                evaluations=tuple(evaluations),
            )
        )
        validation_start += validation_size

    return WalkForwardReport(
        evaluation_version=evaluation_policy.version,
        evaluation_configuration_sha256=evaluation_policy.sha256,
        strategy_version=strategy_policy.version,
        strategy_configuration_sha256=strategy_policy.sha256,
        outcome_version=outcome_policy.version,
        outcome_configuration_sha256=outcome_policy.sha256,
        feature_version=feature_version,
        feature_configuration_sha256=feature_configuration_sha256,
        score_bucket_count=evaluation_policy.score_bucket_count,
        minimum_training_sessions=evaluation_policy.minimum_training_sessions,
        validation_sessions=evaluation_policy.validation_sessions,
        purge_sessions=evaluation_policy.purge_sessions,
        horizons_seconds=outcome_policy.horizons_seconds,
        folds=tuple(folds),
        pending_dates=dates[validation_start:],
    )
