"""Strict, effective-dated configuration for the deterministic trading core."""

from __future__ import annotations

from datetime import date, time
from itertools import pairwise
from pathlib import Path
from typing import Literal
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


def _resolve_effective[Version: _EffectiveVersion](
    values: tuple[Version, ...], value: date, label: str
) -> Version:
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
    quote_depth: int = Field(ge=1, le=10)
    bar_interval_seconds: int = Field(ge=1)
    sessions: SessionScheduleConfiguration

    @model_validator(mode="after")
    def validate_market(self) -> MarketConfiguration:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown market timezone: {self.timezone}") from error
        for name, values in (("symbols", self.symbols), ("indices", self.indices)):
            if (
                not values
                or len(values) != len(set(values))
                or any(not value or value != value.strip().upper() for value in values)
            ):
                raise ValueError(f"{name} must contain unique uppercase identifiers")
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


DecisionSessionName = Literal[
    "opening_auction",
    "continuous_am",
    "continuous_pm",
    "closing_auction",
]

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


class TradingVersion(_EffectiveVersion):
    market: MarketConfiguration
    data_quality: DataQualityConfiguration
    features: FeatureConfiguration


class TradingConfiguration(_StrictModel):
    schema_version: int = Field(ge=1)
    versions: tuple[TradingVersion, ...]
    outcomes: tuple[OutcomeVersion, ...]

    @model_validator(mode="after")
    def validate_versions(self) -> TradingConfiguration:
        _validate_effective_versions(self.versions, "configuration")
        _validate_effective_versions(self.outcomes, "outcome")
        return self

    def resolve(self, value: date) -> TradingVersion:
        return _resolve_effective(self.versions, value, "configuration")

    def resolve_outcomes(self, value: date) -> OutcomeVersion:
        return _resolve_effective(self.outcomes, value, "outcome")

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
