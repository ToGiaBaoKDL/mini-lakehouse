"""Purged, multi-session holdout evaluation for fixed buy-first baselines."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.configuration import BaselineEvaluationVersion, EvaluationTier
from t0_trading.identity import canonical_json, sha256
from t0_trading.numeric import rate, ratio
from t0_trading.strategy.baseline_audit import BaselineAuditReport, BaselineHorizonEvaluation
from t0_trading.strategy.baselines import BASELINE_NAMES, BaselineName

_SYMBOLS: tuple[Literal["VIC", "VHM"], ...] = ("VIC", "VHM")


class BaselineHoldoutEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: BaselineName
    symbol: Literal["VIC", "VHM"]
    horizon_seconds: int = Field(ge=1)
    observed_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    eligible_outcome_count: int = Field(ge=0)
    positive_net_count: int = Field(ge=0)
    candidate_rate: Decimal = Field(ge=0, le=1)
    outcome_coverage_rate: Decimal = Field(ge=0, le=1)
    average_gross_return_bps: Decimal | None
    average_conditional_net_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_counts(self) -> BaselineHoldoutEvaluation:
        if not (
            self.positive_net_count
            <= self.eligible_outcome_count
            <= self.candidate_count
            <= self.observed_count
        ):
            raise ValueError("baseline holdout counts are inconsistent")
        if self.candidate_rate != rate(self.candidate_count, self.observed_count) or (
            self.outcome_coverage_rate != rate(self.eligible_outcome_count, self.candidate_count)
        ):
            raise ValueError("baseline holdout rates are inconsistent")
        if (self.eligible_outcome_count == 0) != (self.average_gross_return_bps is None) or (
            self.eligible_outcome_count == 0
        ) != (self.average_conditional_net_return_bps is None):
            raise ValueError("baseline holdout averages require eligible outcomes")
        return self


class BaselineWalkForwardFold(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fold: int = Field(ge=1)
    development_dates: tuple[date, ...] = Field(min_length=1)
    purged_dates: tuple[date, ...] = Field(min_length=1)
    holdout_dates: tuple[date, ...] = Field(min_length=1)
    evaluations: tuple[BaselineHoldoutEvaluation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fold(self) -> BaselineWalkForwardFold:
        groups = (self.development_dates, self.purged_dates, self.holdout_dates)
        if any(tuple(sorted(set(values))) != values for values in groups) or not (
            self.development_dates[-1]
            < self.purged_dates[0]
            <= self.purged_dates[-1]
            < self.holdout_dates[0]
        ):
            raise ValueError("baseline walk-forward dates must be ordered and disjoint")
        keys = {(item.strategy, item.symbol, item.horizon_seconds) for item in self.evaluations}
        if len(keys) != len(self.evaluations):
            raise ValueError("baseline holdout keys must be unique")
        return self


class BaselineWalkForwardReport(BaseModel):
    """Exploratory reports may become shadow candidates, never live-capital strategies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    evaluation_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    evaluation_tier: EvaluationTier
    evaluation_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_version: str
    feature_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    development_sessions: int = Field(ge=2)
    purge_sessions: int = Field(ge=1)
    holdout_sessions: int = Field(ge=1)
    horizons_seconds: tuple[int, ...] = Field(min_length=1)
    folds: tuple[BaselineWalkForwardFold, ...] = Field(min_length=1)
    pending_dates: tuple[date, ...]
    promotion_eligible: bool = False

    @model_validator(mode="after")
    def validate_report(self) -> BaselineWalkForwardReport:
        if self.promotion_eligible:
            raise ValueError("baseline holdout reports do not authorize capital promotion")
        expected = tuple(
            (strategy, symbol, horizon)
            for strategy in BASELINE_NAMES
            for symbol in _SYMBOLS
            for horizon in self.horizons_seconds
        )
        if tuple(sorted(set(self.horizons_seconds))) != self.horizons_seconds:
            raise ValueError("baseline horizons must be unique and ascending")
        for index, fold in enumerate(self.folds, start=1):
            if (
                fold.fold != index
                or len(fold.development_dates) < self.development_sessions
                or len(fold.purged_dates) != self.purge_sessions
                or len(fold.holdout_dates) != self.holdout_sessions
                or tuple(
                    (item.strategy, item.symbol, item.horizon_seconds) for item in fold.evaluations
                )
                != expected
            ):
                raise ValueError("baseline walk-forward fold matrix is inconsistent")
        if self.pending_dates and (
            tuple(sorted(set(self.pending_dates))) != self.pending_dates
            or self.pending_dates[0] <= self.folds[-1].holdout_dates[-1]
        ):
            raise ValueError("baseline pending dates are inconsistent")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


def _weighted_average(rows: Sequence[BaselineHorizonEvaluation], attribute: str) -> Decimal | None:
    eligible = sum(item.eligible_outcome_count for item in rows)
    if eligible == 0:
        return None
    total = sum(
        (getattr(item, attribute) * item.eligible_outcome_count for item in rows),
        Decimal(0),
    )
    return ratio(total, eligible, quantum=Decimal("0.0001"))


def _combine(rows: Sequence[BaselineHorizonEvaluation]) -> BaselineHoldoutEvaluation:
    if not rows:
        raise ValueError("baseline holdout requires daily evaluations")
    identity = {(item.strategy, item.symbol, item.horizon_seconds) for item in rows}
    if len(identity) != 1:
        raise ValueError("baseline holdout rows must share one evaluation key")
    strategy, symbol, horizon = next(iter(identity))
    if strategy not in BASELINE_NAMES or symbol not in _SYMBOLS:
        raise ValueError("baseline holdout identity is unsupported")
    observed = sum(item.observed_count for item in rows)
    candidates = sum(item.candidate_count for item in rows)
    eligible = sum(item.eligible_outcome_count for item in rows)
    return BaselineHoldoutEvaluation(
        strategy=strategy,
        symbol=symbol,
        horizon_seconds=horizon,
        observed_count=observed,
        candidate_count=candidates,
        eligible_outcome_count=eligible,
        positive_net_count=sum(item.positive_net_count for item in rows),
        candidate_rate=rate(candidates, observed),
        outcome_coverage_rate=rate(eligible, candidates),
        average_gross_return_bps=_weighted_average(rows, "average_gross_return_bps"),
        average_conditional_net_return_bps=_weighted_average(
            rows, "average_conditional_net_return_bps"
        ),
    )


def evaluate_baseline_walk_forward(
    sessions: Mapping[date, BaselineAuditReport],
    policy: BaselineEvaluationVersion,
) -> BaselineWalkForwardReport:
    """Evaluate already-frozen formulas only on purged, untouched holdout sessions."""
    dates = tuple(sorted(sessions))
    required = policy.development_sessions + policy.purge_sessions + policy.holdout_sessions
    if len(dates) < required:
        raise ValueError(f"baseline walk-forward requires at least {required} sessions")
    if any(not policy.contains(trade_date) for trade_date in dates):
        raise ValueError("baseline evaluation policy must cover every session")
    reports = tuple(sessions[trade_date] for trade_date in dates)
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
    if len(lineages) != 1 or next(iter(lineages))[2] is None:
        raise ValueError("baseline sessions must share complete research lineage")
    horizons = tuple(sorted({item.horizon_seconds for item in reports[0].evaluations}))
    expected_keys = tuple(
        (strategy, symbol, horizon)
        for strategy in BASELINE_NAMES
        for symbol in _SYMBOLS
        for horizon in horizons
    )
    by_date: dict[date, dict[tuple[str, str, int], BaselineHorizonEvaluation]] = {}
    for trade_date, report in sessions.items():
        keyed = {
            (item.strategy, item.symbol, item.horizon_seconds): item for item in report.evaluations
        }
        if tuple(keyed) != expected_keys or report.trade_date != trade_date:
            raise ValueError("baseline session does not contain the complete ordered matrix")
        by_date[trade_date] = keyed

    folds: list[BaselineWalkForwardFold] = []
    holdout_start = policy.development_sessions + policy.purge_sessions
    while holdout_start + policy.holdout_sessions <= len(dates):
        development_end = holdout_start - policy.purge_sessions
        development = dates[:development_end]
        purged = dates[development_end:holdout_start]
        holdout = dates[holdout_start : holdout_start + policy.holdout_sessions]
        evaluations = tuple(
            _combine(tuple(by_date[trade_date][key] for trade_date in holdout))
            for key in expected_keys
        )
        folds.append(
            BaselineWalkForwardFold(
                fold=len(folds) + 1,
                development_dates=development,
                purged_dates=purged,
                holdout_dates=holdout,
                evaluations=evaluations,
            )
        )
        holdout_start += policy.holdout_sessions
    last_holdout = folds[-1].holdout_dates[-1]
    lineage = next(iter(lineages))
    return BaselineWalkForwardReport(
        evaluation_version=policy.version,
        evaluation_tier=policy.tier,
        evaluation_configuration_sha256=policy.sha256,
        baseline_version=lineage[0],
        feature_configuration_sha256=lineage[1],
        context_configuration_sha256=cast(str, lineage[2]),
        outcome_configuration_sha256=lineage[3],
        cost_policy_sha256=lineage[4],
        development_sessions=policy.development_sessions,
        purge_sessions=policy.purge_sessions,
        holdout_sessions=policy.holdout_sessions,
        horizons_seconds=horizons,
        folds=tuple(folds),
        pending_dates=tuple(item for item in dates if item > last_holdout),
    )
