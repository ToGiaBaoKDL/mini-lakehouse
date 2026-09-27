"""Conditional gross and fee-adjusted audits for buy-first research baselines."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.context import ContextDataMode, MarketRegime
from t0_trading.controls import CostPolicy, conditional_net_return_bps
from t0_trading.identity import canonical_json, sha256
from t0_trading.numeric import rate, ratio
from t0_trading.outcomes import OutcomeLabel
from t0_trading.strategy.baselines import (
    BASELINE_GROUP_NAMES,
    BASELINE_NAMES,
    BaselineCandidate,
    BaselineName,
)

_SYMBOLS = ("VIC", "VHM")


class BaselineHorizonEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: BaselineName
    symbol: Literal["VIC", "VHM"]
    horizon_seconds: int = Field(ge=1)
    observed_count: int = Field(ge=0)
    raw_candidate_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    eligible_outcome_count: int = Field(ge=0)
    positive_gross_count: int = Field(ge=0)
    positive_net_count: int = Field(ge=0)
    candidate_rate: Decimal = Field(ge=0, le=1)
    outcome_coverage_rate: Decimal = Field(ge=0, le=1)
    average_gross_return_bps: Decimal | None
    average_conditional_net_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_counts(self) -> BaselineHorizonEvaluation:
        if not (
            self.positive_gross_count
            <= self.eligible_outcome_count
            <= self.candidate_count
            <= self.raw_candidate_count
            <= self.observed_count
            and self.positive_net_count <= self.eligible_outcome_count
        ):
            raise ValueError("baseline audit counts are inconsistent")
        if self.candidate_rate != rate(self.candidate_count, self.observed_count) or (
            self.outcome_coverage_rate != rate(self.eligible_outcome_count, self.candidate_count)
        ):
            raise ValueError("baseline audit rates are inconsistent")
        if (self.eligible_outcome_count == 0) != (self.average_gross_return_bps is None) or (
            self.eligible_outcome_count == 0
        ) != (self.average_conditional_net_return_bps is None):
            raise ValueError("baseline audit averages require eligible outcomes")
        return self


class BaselineGroupCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: BaselineName
    symbol: Literal["VIC", "VHM"]
    group: str
    observed_count: int = Field(ge=0)
    available_count: int = Field(ge=0)
    matched_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> BaselineGroupCoverage:
        if not self.matched_count <= self.available_count <= self.observed_count:
            raise ValueError("baseline group coverage counts are inconsistent")
        return self


class BaselineRegimeEvaluation(BaseModel):
    """Conditional performance for one preclassified point-in-time market regime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: BaselineName
    symbol: Literal["VIC", "VHM"]
    regime: MarketRegime
    horizon_seconds: int = Field(ge=1)
    candidate_count: int = Field(ge=0)
    eligible_outcome_count: int = Field(ge=0)
    positive_net_count: int = Field(ge=0)
    average_gross_return_bps: Decimal | None
    average_conditional_net_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_counts(self) -> BaselineRegimeEvaluation:
        if not self.positive_net_count <= self.eligible_outcome_count <= self.candidate_count:
            raise ValueError("baseline regime counts are inconsistent")
        if (self.eligible_outcome_count == 0) != (self.average_gross_return_bps is None) or (
            self.eligible_outcome_count == 0
        ) != (self.average_conditional_net_return_bps is None):
            raise ValueError("baseline regime averages require eligible outcomes")
        return self


class BaselineAuditReport(BaseModel):
    """No portfolio or fill claims: these are conditional BUY→SELL markouts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[3] = 3
    baseline_version: str
    trade_date: date
    capture_evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    feature_version: str
    feature_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_version: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")
    context_configuration_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    context_data_mode: ContextDataMode | None = None
    outcome_version: str
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_policy: CostPolicy
    cost_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonoverlap_seconds: int = Field(ge=1)
    context_clock_count: int = Field(ge=1)
    context_regime_counts: dict[MarketRegime, int]
    group_coverage: tuple[BaselineGroupCoverage, ...]
    evaluations: tuple[BaselineHorizonEvaluation, ...]
    regime_evaluations: tuple[BaselineRegimeEvaluation, ...]

    @model_validator(mode="after")
    def validate_policy_identity(self) -> BaselineAuditReport:
        context_lineage = (
            self.context_version,
            self.context_configuration_sha256,
            self.context_data_mode,
        )
        if any(value is None for value in context_lineage) and any(
            value is not None for value in context_lineage
        ):
            raise ValueError("baseline context lineage must be wholly present or absent")
        if (
            any(count < 0 for count in self.context_regime_counts.values())
            or sum(self.context_regime_counts.values()) != self.context_clock_count
        ):
            raise ValueError("baseline context regime counts are inconsistent")
        if self.cost_policy_sha256 != sha256(
            canonical_json(self.cost_policy.model_dump(mode="json"))
        ):
            raise ValueError("baseline cost policy hash is inconsistent")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


def _average(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return ratio(sum(values, Decimal(0)), len(values), quantum=Decimal("0.0001"))


def _priced_outcomes(
    candidates: Sequence[BaselineCandidate],
    horizon: int,
    symbol: str,
    label_by_key: dict[tuple[str, int], OutcomeLabel],
    feature_lineage: tuple[str, str],
    costs: CostPolicy,
) -> tuple[list[OutcomeLabel], list[Decimal], list[Decimal]]:
    eligible: list[OutcomeLabel] = []
    gross: list[Decimal] = []
    for candidate in candidates:
        label = label_by_key[(candidate.feature_snapshot_sha256, horizon)]
        if (
            label.symbol != symbol
            or label.decision_at != candidate.decision_at
            or (label.feature_version, label.feature_configuration_sha256) != feature_lineage
        ):
            raise ValueError("baseline outcome does not match candidate lineage")
        if label.is_eligible:
            if (
                label.entry_vwap is None
                or label.horizon_vwap is None
                or label.gross_return_bps is None
            ):
                raise ValueError("eligible baseline outcome must be priced")
            eligible.append(label)
            gross.append(label.gross_return_bps)
    return eligible, gross, [conditional_net_return_bps(label, costs) for label in eligible]


def evaluate_buy_first_baselines(
    candidates: Sequence[BaselineCandidate],
    labels: Sequence[OutcomeLabel],
    costs: CostPolicy,
    *,
    capture_evidence_sha256: str | None = None,
) -> BaselineAuditReport:
    """Audit every baseline/symbol/horizon using exact pre-outcome lineage."""
    if not candidates:
        raise ValueError("baseline audit requires candidates or abstentions")
    dates = {item.trade_date for item in candidates}
    if len(dates) != 1:
        raise ValueError("baseline audit requires one trade date")
    trade_date = next(iter(dates))
    if not costs.contains(trade_date):
        raise ValueError("cost policy is not effective for the trade date")
    candidate_keys = [(item.strategy, item.symbol, item.decision_at) for item in candidates]
    if len(set(candidate_keys)) != len(candidate_keys):
        raise ValueError("baseline observations must be unique")
    by_clock: dict[tuple[str, datetime], list[BaselineCandidate]] = {}
    for candidate in candidates:
        by_clock.setdefault((candidate.symbol, candidate.decision_at), []).append(candidate)
    if any(
        {item.strategy for item in group} != set(BASELINE_NAMES)
        or len({item.feature_snapshot_sha256 for item in group}) != 1
        or len({item.context_snapshot_sha256 for item in group}) != 1
        for group in by_clock.values()
    ):
        raise ValueError("baseline observations must contain the complete strategy matrix")
    decision_contexts: dict[datetime, tuple[str | None, MarketRegime]] = {}
    for candidate in candidates:
        value = (candidate.context_snapshot_sha256, candidate.market_regime)
        previous = decision_contexts.setdefault(candidate.decision_at, value)
        if previous != value:
            raise ValueError("baseline observations disagree on decision context")
    if any(item.baseline_version != candidates[0].baseline_version for item in candidates):
        raise ValueError("baseline versions are inconsistent")
    context_lineages: set[tuple[str | None, str | None, ContextDataMode | None]] = {
        (
            item.context_version,
            item.context_configuration_sha256,
            item.context_data_mode,
        )
        for item in candidates
    }
    if len(context_lineages) != 1:
        raise ValueError("baseline context lineages are inconsistent")

    buy_labels = [label for label in labels if label.action == "BUY"]
    if not buy_labels or any(label.trade_date != trade_date for label in buy_labels):
        raise ValueError("BUY outcomes must cover the baseline trade date")
    outcome_lineages = {
        (label.outcome_version, label.outcome_configuration_sha256) for label in buy_labels
    }
    feature_lineages = {
        (label.feature_version, label.feature_configuration_sha256) for label in buy_labels
    }
    if len(outcome_lineages) != 1 or len(feature_lineages) != 1:
        raise ValueError("baseline outcome lineage must be homogeneous")
    label_by_key = {
        (label.feature_snapshot_sha256, label.horizon_seconds): label for label in buy_labels
    }
    if len(label_by_key) != len(buy_labels):
        raise ValueError("baseline BUY outcomes must be unique")
    horizons = tuple(sorted({label.horizon_seconds for label in buy_labels}))
    if any(
        item.is_candidate
        and any((item.feature_snapshot_sha256, horizon) not in label_by_key for horizon in horizons)
        for item in candidates
    ):
        raise ValueError("baseline outcomes must cover every candidate horizon")

    evaluations: list[BaselineHorizonEvaluation] = []
    regime_evaluations: list[BaselineRegimeEvaluation] = []
    group_coverage: list[BaselineGroupCoverage] = []
    nonoverlap_seconds = max(horizons)
    for strategy in BASELINE_NAMES:
        for symbol in _SYMBOLS:
            observed = [
                item for item in candidates if item.strategy == strategy and item.symbol == symbol
            ]
            for group_index in range(3):
                groups = [item.groups[group_index] for item in observed]
                group_coverage.append(
                    BaselineGroupCoverage(
                        strategy=strategy,
                        symbol=symbol,
                        group=BASELINE_GROUP_NAMES[strategy][group_index],
                        observed_count=len(groups),
                        available_count=sum(group.status != "UNAVAILABLE" for group in groups),
                        matched_count=sum(group.status == "MATCH" for group in groups),
                    )
                )
            raw_candidates = sorted(
                (item for item in observed if item.is_candidate),
                key=lambda item: item.decision_at,
            )
            selected: list[BaselineCandidate] = []
            next_eligible_at = None
            for candidate in raw_candidates:
                if next_eligible_at is None or candidate.decision_at >= next_eligible_at:
                    selected.append(candidate)
                    next_eligible_at = candidate.decision_at + timedelta(seconds=nonoverlap_seconds)
            for horizon in horizons:
                eligible, gross, net = _priced_outcomes(
                    selected,
                    horizon,
                    symbol,
                    label_by_key,
                    next(iter(feature_lineages)),
                    costs,
                )
                evaluations.append(
                    BaselineHorizonEvaluation(
                        strategy=strategy,
                        symbol=symbol,
                        horizon_seconds=horizon,
                        observed_count=len(observed),
                        raw_candidate_count=len(raw_candidates),
                        candidate_count=len(selected),
                        eligible_outcome_count=len(eligible),
                        positive_gross_count=sum(value > 0 for value in gross),
                        positive_net_count=sum(value > 0 for value in net),
                        candidate_rate=rate(len(selected), len(observed)),
                        outcome_coverage_rate=rate(len(eligible), len(selected)),
                        average_gross_return_bps=_average(gross),
                        average_conditional_net_return_bps=_average(net),
                    )
                )
                regimes: set[MarketRegime] = {candidate.market_regime for candidate in selected}
                for regime in sorted(regimes):
                    regime_candidates = [
                        candidate for candidate in selected if candidate.market_regime == regime
                    ]
                    regime_eligible, regime_gross, regime_net = _priced_outcomes(
                        regime_candidates,
                        horizon,
                        symbol,
                        label_by_key,
                        next(iter(feature_lineages)),
                        costs,
                    )
                    regime_evaluations.append(
                        BaselineRegimeEvaluation(
                            strategy=strategy,
                            symbol=symbol,
                            regime=regime,
                            horizon_seconds=horizon,
                            candidate_count=len(regime_candidates),
                            eligible_outcome_count=len(regime_eligible),
                            positive_net_count=sum(value > 0 for value in regime_net),
                            average_gross_return_bps=_average(regime_gross),
                            average_conditional_net_return_bps=_average(regime_net),
                        )
                    )
    outcome_version, outcome_sha = next(iter(outcome_lineages))
    feature_version, feature_sha = next(iter(feature_lineages))
    context_version, context_sha, context_data_mode = next(iter(context_lineages))
    regime_counts: dict[MarketRegime, int] = {}
    for _, regime in decision_contexts.values():
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
    return BaselineAuditReport(
        baseline_version=candidates[0].baseline_version,
        trade_date=trade_date,
        capture_evidence_sha256=capture_evidence_sha256,
        feature_version=feature_version,
        feature_configuration_sha256=feature_sha,
        context_version=context_version,
        context_configuration_sha256=context_sha,
        context_data_mode=context_data_mode,
        outcome_version=outcome_version,
        outcome_configuration_sha256=outcome_sha,
        cost_policy=costs,
        cost_policy_sha256=sha256(canonical_json(costs.model_dump(mode="json"))),
        nonoverlap_seconds=nonoverlap_seconds,
        context_clock_count=len(decision_contexts),
        context_regime_counts=regime_counts,
        group_coverage=tuple(group_coverage),
        evaluations=tuple(evaluations),
        regime_evaluations=tuple(regime_evaluations),
    )
