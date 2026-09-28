"""Strict, effective-dated configuration for the deterministic trading core."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.identity import canonical_json, sha256


class TradingConfigurationError(ValueError):
    """The trading configuration is invalid or has no effective version."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _EffectiveVersion(_StrictModel):
    version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    effective_from: date
    effective_to: date | None

    @model_validator(mode="after")
    def validate_interval(self) -> _EffectiveVersion:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        return self

    def contains(self, value: date) -> bool:
        return self.effective_from <= value and (
            self.effective_to is None or value <= self.effective_to
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


_EffectiveVersionT = TypeVar("_EffectiveVersionT", bound=_EffectiveVersion)


def _require_identifiers(name: str, values: tuple[str, ...]) -> None:
    if (
        not values
        or len(values) != len(set(values))
        or any(not value or value != value.strip().upper() for value in values)
    ):
        raise ValueError(f"{name} must contain unique uppercase identifiers")


def _validate_effective_versions(values: tuple[_EffectiveVersion, ...], label: str) -> None:
    ordered = sorted(values, key=lambda item: item.effective_from)
    if not ordered or tuple(ordered) != values:
        raise ValueError(f"{label} versions must be non-empty and ordered by effective_from")
    if len({item.version for item in ordered}) != len(ordered):
        raise ValueError(f"{label} version names must be unique")
    if any(
        previous.effective_to is None or previous.effective_to >= current.effective_from
        for previous, current in pairwise(ordered)
    ):
        raise ValueError(f"{label} effective intervals must not overlap")


def _intervals_overlap(left: _EffectiveVersion, right: _EffectiveVersion) -> bool:
    return (left.effective_to is None or right.effective_from <= left.effective_to) and (
        right.effective_to is None or left.effective_from <= right.effective_to
    )


# Keep Python 3.11 syntax because this package is bundled into the EMR runtime.
def _resolve_effective(  # noqa: UP047
    values: tuple[_EffectiveVersionT, ...], value: date, label: str
) -> _EffectiveVersionT:
    matches = tuple(version for version in values if version.contains(value))
    if len(matches) != 1:
        raise TradingConfigurationError(
            f"expected one {label} version for {value.isoformat()}, found {len(matches)}"
        )
    return matches[0]


class SessionScheduleConfiguration(_StrictModel):
    opening_auction: tuple[time, time]
    continuous_am: tuple[time, time]
    continuous_pm: tuple[time, time]
    closing_auction: tuple[time, time]

    @model_validator(mode="after")
    def validate_windows(self) -> SessionScheduleConfiguration:
        windows = (
            self.opening_auction,
            self.continuous_am,
            self.continuous_pm,
            self.closing_auction,
        )
        if any(value.tzinfo is not None for window in windows for value in window):
            raise ValueError("market session times must be timezone-naive")
        if any(start >= end for start, end in windows):
            raise ValueError("market session windows must have positive duration")
        if (
            self.opening_auction[1] != self.continuous_am[0]
            or self.continuous_am[1] >= self.continuous_pm[0]
            or self.continuous_pm[1] != self.closing_auction[0]
        ):
            raise ValueError("market session windows must be ordered and non-overlapping")
        return self


class MarketConfiguration(_StrictModel):
    timezone: str
    symbols: tuple[str, ...]
    indices: tuple[str, ...]
    status_markets: tuple[str, ...]
    quote_depth: int = Field(ge=1, le=10)
    bar_interval_seconds: int = Field(ge=1)
    sessions: SessionScheduleConfiguration

    @model_validator(mode="after")
    def validate_market(self) -> MarketConfiguration:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown market timezone: {self.timezone}") from error
        for name, values in (
            ("symbols", self.symbols),
            ("indices", self.indices),
            ("status_markets", self.status_markets),
        ):
            _require_identifiers(name, values)
        if 60 % self.bar_interval_seconds != 0 and self.bar_interval_seconds % 60 != 0:
            raise ValueError("bar_interval_seconds must align to a minute boundary")
        session_times = (
            value
            for window in (
                self.sessions.opening_auction,
                self.sessions.continuous_am,
                self.sessions.continuous_pm,
                self.sessions.closing_auction,
            )
            for value in window
        )
        if any(
            (value.hour * 3600 + value.minute * 60 + value.second) % self.bar_interval_seconds
            for value in session_times
        ):
            raise ValueError("market session boundaries must align to bar_interval_seconds")
        return self


class DataQualityConfiguration(_StrictModel):
    trade_stale_after_seconds: int = Field(ge=1)
    quote_stale_after_seconds: int = Field(ge=1)


class CaptureConfiguration(_StrictModel):
    """Operational evidence scope, independent from decision requirements."""

    symbols: tuple[str, ...]
    indices: tuple[str, ...]
    membership_indices: tuple[str, ...]
    markets: tuple[str, ...]

    @model_validator(mode="after")
    def validate_scope(self) -> CaptureConfiguration:
        for name, values in (
            ("symbols", self.symbols),
            ("indices", self.indices),
            ("membership_indices", self.membership_indices),
            ("markets", self.markets),
        ):
            _require_identifiers(f"capture {name}", values)
        if set(self.symbols) & set(self.indices):
            raise ValueError("capture symbols and indices must be disjoint")
        if not set(self.membership_indices).issubset(self.indices):
            raise ValueError("capture membership indices must be captured indices")
        return self


DecisionSessionName = Literal[
    "opening_auction",
    "continuous_am",
    "continuous_pm",
    "closing_auction",
]

EvaluationTier = Literal["EXPLORATORY", "PROMOTION"]

_SESSION_ORDER: tuple[DecisionSessionName, ...] = (
    "opening_auction",
    "continuous_am",
    "continuous_pm",
    "closing_auction",
)


class FeatureConfiguration(_StrictModel):
    version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    cadence_seconds: int = Field(ge=1, le=60)
    windows_seconds: tuple[int, ...]
    warmup_seconds: int = Field(ge=1, le=86_400)
    decision_sessions: tuple[DecisionSessionName, ...]

    @model_validator(mode="after")
    def validate_features(self) -> FeatureConfiguration:
        if 60 % self.cadence_seconds:
            raise ValueError("feature cadence must divide one minute")
        if (
            not self.windows_seconds
            or tuple(sorted(set(self.windows_seconds))) != self.windows_seconds
            or any(
                window < self.cadence_seconds or window > 86_400 or window % self.cadence_seconds
                for window in self.windows_seconds
            )
        ):
            raise ValueError("feature windows must be unique ascending cadence multiples")
        if (
            self.warmup_seconds < self.windows_seconds[-1]
            or self.warmup_seconds % self.cadence_seconds
        ):
            raise ValueError("feature warmup must cover every window and align to cadence")
        if not self.decision_sessions or len(set(self.decision_sessions)) != len(
            self.decision_sessions
        ):
            raise ValueError("feature decision sessions must be unique and non-empty")
        if (
            tuple(sorted(self.decision_sessions, key=_SESSION_ORDER.index))
            != self.decision_sessions
        ):
            raise ValueError("feature decision sessions must follow market-session order")
        return self


class OutcomeVersion(_EffectiveVersion):
    """Effective research assumptions for conditional Top-3 execution markouts."""

    horizons_seconds: tuple[int, ...]
    order_quantity: int = Field(ge=1)
    execution_latency_milliseconds: int = Field(ge=0, le=60_000)

    @model_validator(mode="after")
    def validate_outcomes(self) -> OutcomeVersion:
        if (
            not self.horizons_seconds
            or tuple(sorted(set(self.horizons_seconds))) != self.horizons_seconds
            or any(horizon < 1 or horizon > 86_400 for horizon in self.horizons_seconds)
        ):
            raise ValueError("outcome horizons must be unique ascending positive seconds")
        if self.execution_latency_milliseconds >= self.horizons_seconds[0] * 1_000:
            raise ValueError("execution latency must precede every outcome horizon")
        return self


class BaselineEvaluationVersion(_EffectiveVersion):
    """Session isolation policy for fixed, buy-first baseline hypotheses."""

    tier: EvaluationTier
    development_sessions: int = Field(ge=2)
    holdout_sessions: int = Field(ge=1)
    purge_sessions: int = Field(ge=1)


class CandidateArbitrationRule(_StrictModel):
    """Configured strategy precedence; the engine contains no strategy names."""

    strategy: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    priority: int = Field(ge=0)
    minimum_strength: Decimal = Field(ge=0, le=1)
    horizon_seconds: int = Field(ge=1, le=86_400)


class CandidateArbitrationVersion(_EffectiveVersion):
    """Prospective, outcome-blind policy for selecting research candidates."""

    candidate_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    cooldown_seconds: int = Field(ge=0, le=86_400)
    maximum_selections_per_clock: int = Field(ge=1)
    rules: tuple[CandidateArbitrationRule, ...]

    @model_validator(mode="after")
    def validate_rules(self) -> CandidateArbitrationVersion:
        strategies = tuple(rule.strategy for rule in self.rules)
        priorities = tuple(rule.priority for rule in self.rules)
        if not strategies or len(set(strategies)) != len(strategies):
            raise ValueError("candidate arbitration strategies must be unique and non-empty")
        if tuple(sorted(priorities)) != tuple(range(len(priorities))):
            raise ValueError("candidate arbitration priorities must be contiguous from zero")
        if self.cooldown_seconds < max(rule.horizon_seconds for rule in self.rules):
            raise ValueError("candidate arbitration cooldown must cover every selected horizon")
        return self


class PromotionTarget(_StrictModel):
    """One fixed baseline variant eligible for evidence-based promotion."""

    strategy: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]*$")
    horizon_seconds: int = Field(ge=1, le=86_400)


class PromotionGateVersion(_EffectiveVersion):
    """Prospective thresholds for moving a fixed hypothesis from shadow to paper."""

    scope: Literal["SHADOW_TO_PAPER"] = "SHADOW_TO_PAPER"
    evaluation_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    minimum_selected_count: int = Field(ge=1)
    minimum_outcome_coverage_rate: Decimal = Field(ge=0, le=1)
    minimum_positive_net_rate: Decimal = Field(ge=0, le=1)
    minimum_average_net_return_bps: Decimal
    minimum_worst_fold_net_return_bps: Decimal
    maximum_capture_gap_count: int = Field(ge=0)
    targets: tuple[PromotionTarget, ...]

    @model_validator(mode="after")
    def validate_targets(self) -> PromotionGateVersion:
        keys = tuple(
            (target.strategy, target.symbol, target.horizon_seconds) for target in self.targets
        )
        if not keys or len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
            raise ValueError("promotion targets must be unique, non-empty, and ordered")
        if self.minimum_worst_fold_net_return_bps > self.minimum_average_net_return_bps:
            raise ValueError("worst-fold threshold cannot exceed the aggregate threshold")
        return self


class PaperExecutionVersion(_EffectiveVersion):
    """Broker-neutral assumptions for deterministic paper order planning."""

    arbitration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    promotion_gate_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    context_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    order_quantity: int = Field(ge=1)
    lot_size: int = Field(ge=1)
    maximum_quote_age_seconds: int = Field(ge=1, le=300)
    limit_offset_ticks: int = Field(ge=0, le=100)
    time_in_force_seconds: int = Field(ge=1, le=300)

    @model_validator(mode="after")
    def validate_execution(self) -> PaperExecutionVersion:
        if self.order_quantity % self.lot_size:
            raise ValueError("paper order quantity must align to lot size")
        return self


class ContextVersion(_EffectiveVersion):
    """Point-in-time zone and broad-market research assumptions."""

    zone_lookback_seconds: int = Field(ge=60, le=86_400)
    zone_minimum_observations: int = Field(ge=2)
    zone_tolerance_bps: Decimal = Field(gt=0)
    market_windows_seconds: tuple[int, int]
    index_stale_after_seconds: int = Field(ge=1, le=900)
    historical_proxy_interval_seconds: int = Field(ge=1, le=900)
    historical_proxy_stale_after_seconds: int = Field(ge=1, le=900)
    tradable_market_statuses: tuple[str, ...]
    trend_threshold_bps: Decimal = Field(gt=0)
    high_volatility_threshold_bps: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_context(self) -> ContextVersion:
        if tuple(sorted(set(self.market_windows_seconds))) != self.market_windows_seconds or any(
            window < 1 or window > self.zone_lookback_seconds
            for window in self.market_windows_seconds
        ):
            raise ValueError("context market windows must be unique ascending lookback bounds")
        if self.historical_proxy_stale_after_seconds < self.historical_proxy_interval_seconds:
            raise ValueError(
                "historical proxy staleness must cover its conservative availability delay"
            )
        _require_identifiers("tradable_market_statuses", self.tradable_market_statuses)
        return self


class TradingVersion(_EffectiveVersion):
    market: MarketConfiguration
    data_quality: DataQualityConfiguration
    features: FeatureConfiguration


class TradingConfiguration(_StrictModel):
    schema_version: Literal[2] = 2
    capture: CaptureConfiguration
    versions: tuple[TradingVersion, ...]
    outcomes: tuple[OutcomeVersion, ...]
    baseline_evaluations: tuple[BaselineEvaluationVersion, ...]
    candidate_arbitrations: tuple[CandidateArbitrationVersion, ...]
    promotion_gates: tuple[PromotionGateVersion, ...]
    paper_executions: tuple[PaperExecutionVersion, ...]
    contexts: tuple[ContextVersion, ...]

    @model_validator(mode="after")
    def validate_versions(self) -> TradingConfiguration:
        _validate_effective_versions(self.versions, "configuration")
        _validate_effective_versions(self.outcomes, "outcome")
        for tier in ("EXPLORATORY", "PROMOTION"):
            _validate_effective_versions(
                tuple(item for item in self.baseline_evaluations if item.tier == tier),
                f"{tier.lower()} baseline evaluation",
            )
        if tuple(
            sorted(self.baseline_evaluations, key=lambda item: (item.tier, item.effective_from))
        ) != self.baseline_evaluations or len(
            {item.version for item in self.baseline_evaluations}
        ) != len(self.baseline_evaluations):
            raise ValueError("baseline evaluation versions must be unique and canonically ordered")
        _validate_effective_versions(self.candidate_arbitrations, "candidate arbitration")
        _validate_effective_versions(self.promotion_gates, "promotion gate")
        _validate_effective_versions(self.paper_executions, "paper execution")
        _validate_effective_versions(self.contexts, "context")
        for arbitration in self.candidate_arbitrations:
            overlapping_outcomes = tuple(
                outcome for outcome in self.outcomes if _intervals_overlap(arbitration, outcome)
            )
            horizons = {rule.horizon_seconds for rule in arbitration.rules}
            if not overlapping_outcomes or any(
                not horizons.issubset(outcome.horizons_seconds) for outcome in overlapping_outcomes
            ):
                raise ValueError("candidate arbitration horizons require matching outcome labels")
        evaluation_versions = {
            item.version: item for item in self.baseline_evaluations if item.tier == "PROMOTION"
        }
        arbitration_versions = {item.version: item for item in self.candidate_arbitrations}
        for gate in self.promotion_gates:
            evaluation = evaluation_versions.get(gate.evaluation_version)
            arbitration = arbitration_versions.get(gate.arbitration_version)
            if evaluation is None or arbitration is None:
                raise ValueError("promotion gate references an unknown policy version")
            if not evaluation.contains(gate.effective_from) or not arbitration.contains(
                gate.effective_from
            ):
                raise ValueError("promotion gate starts outside its referenced policies")
            if gate.effective_to is not None and (
                not evaluation.contains(gate.effective_to)
                or not arbitration.contains(gate.effective_to)
            ):
                raise ValueError("promotion gate ends outside its referenced policies")
            rules = {rule.strategy: rule for rule in arbitration.rules}
            if any(
                target.strategy not in rules
                or target.horizon_seconds != rules[target.strategy].horizon_seconds
                for target in gate.targets
            ):
                raise ValueError("promotion targets do not match arbitration rules")
            overlapping_markets = tuple(
                version for version in self.versions if _intervals_overlap(gate, version)
            )
            if not overlapping_markets or any(
                any(target.symbol not in version.market.symbols for target in gate.targets)
                for version in overlapping_markets
            ):
                raise ValueError("promotion targets do not match the effective market universe")
        gate_versions = {item.version: item for item in self.promotion_gates}
        context_versions = {item.version: item for item in self.contexts}
        for execution in self.paper_executions:
            arbitration = arbitration_versions.get(execution.arbitration_version)
            gate = gate_versions.get(execution.promotion_gate_version)
            context = context_versions.get(execution.context_version)
            if arbitration is None or gate is None or context is None:
                raise ValueError("paper execution references an unknown policy version")
            referenced = (arbitration, gate, context)
            if gate.arbitration_version != arbitration.version or any(
                not item.contains(execution.effective_from)
                or (
                    execution.effective_to is not None and not item.contains(execution.effective_to)
                )
                for item in referenced
            ):
                raise ValueError("paper execution falls outside its referenced policies")
        return self

    def resolve(self, value: date) -> TradingVersion:
        return _resolve_effective(self.versions, value, "configuration")

    def capture_scope(self, value: date) -> CaptureConfiguration:
        """Return a scope proven to cover the effective decision universe."""
        market = self.resolve(value).market
        if (
            not set(market.symbols).issubset(self.capture.symbols)
            or not set(market.indices).issubset(self.capture.indices)
            or not set(market.status_markets).issubset(self.capture.markets)
        ):
            raise TradingConfigurationError(
                f"capture scope does not cover the decision universe for {value.isoformat()}"
            )
        return self.capture

    def resolve_outcomes(self, value: date) -> OutcomeVersion:
        return _resolve_effective(self.outcomes, value, "outcome")

    def resolve_context(self, value: date) -> ContextVersion:
        return _resolve_effective(self.contexts, value, "context")

    def resolve_candidate_arbitration(self, value: date) -> CandidateArbitrationVersion | None:
        """Return the prospective policy, or none before arbitration was declared."""
        return self._resolve_optional(
            self.candidate_arbitrations,
            value,
            "candidate arbitration",
        )

    def resolve_promotion_gate(self, value: date) -> PromotionGateVersion | None:
        """Return the prospective shadow-to-paper gate, if one has been declared."""
        return self._resolve_optional(self.promotion_gates, value, "promotion gate")

    def resolve_paper_execution(self, value: date) -> PaperExecutionVersion | None:
        """Return the broker-neutral paper policy, if one has been declared."""
        return self._resolve_optional(self.paper_executions, value, "paper execution")

    @staticmethod
    def _resolve_optional(
        values: tuple[_EffectiveVersionT, ...], value: date, label: str
    ) -> _EffectiveVersionT | None:
        matches = tuple(item for item in values if item.contains(value))
        if len(matches) > 1:
            raise TradingConfigurationError(
                f"expected at most one {label} version for {value.isoformat()}"
            )
        if matches:
            return matches[0]
        if value < values[0].effective_from:
            return None
        raise TradingConfigurationError(
            f"expected one {label} version for {value.isoformat()}, found 0"
        )

    def resolve_baseline_evaluation(
        self,
        value: date,
        tier: EvaluationTier = "PROMOTION",
    ) -> BaselineEvaluationVersion:
        return _resolve_effective(
            tuple(item for item in self.baseline_evaluations if item.tier == tier),
            value,
            f"{tier.lower()} baseline evaluation",
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


def parse_configuration(content: str) -> TradingConfiguration:
    """Validate one complete YAML document without owning its transport."""
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise TradingConfigurationError("cannot parse trading configuration") from error
    try:
        return TradingConfiguration.model_validate(payload)
    except ValueError as error:
        raise TradingConfigurationError("invalid trading configuration") from error


def load_configuration(path: Path) -> TradingConfiguration:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise TradingConfigurationError(f"cannot read trading configuration: {path}") from error
    return parse_configuration(content)
