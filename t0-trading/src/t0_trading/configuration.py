"""Strict, effective-dated configuration for the deterministic trading core."""

from __future__ import annotations

import hashlib
import json
from datetime import date, time
from itertools import pairwise
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TradingConfigurationError(ValueError):
    """The trading configuration is invalid or has no effective version."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class TradingVersion(_StrictModel):
    version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    effective_from: date
    effective_to: date | None
    market: MarketConfiguration
    data_quality: DataQualityConfiguration
    features: FeatureConfiguration

    @model_validator(mode="after")
    def validate_interval(self) -> TradingVersion:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        return self

    def contains(self, value: date) -> bool:
        return self.effective_from <= value and (
            self.effective_to is None or value <= self.effective_to
        )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class TradingConfiguration(_StrictModel):
    schema_version: int = Field(ge=1)
    versions: tuple[TradingVersion, ...]

    @model_validator(mode="after")
    def validate_versions(self) -> TradingConfiguration:
        if not self.versions:
            raise ValueError("at least one configuration version is required")
        ordered = sorted(self.versions, key=lambda item: item.effective_from)
        if tuple(ordered) != self.versions:
            raise ValueError("configuration versions must be ordered by effective_from")
        if len({item.version for item in ordered}) != len(ordered):
            raise ValueError("configuration version names must be unique")
        for previous, current in pairwise(ordered):
            if previous.effective_to is None or previous.effective_to >= current.effective_from:
                raise ValueError("configuration effective intervals must not overlap")
        return self

    def resolve(self, value: date) -> TradingVersion:
        matches = tuple(version for version in self.versions if version.contains(value))
        if len(matches) != 1:
            raise TradingConfigurationError(
                f"expected one configuration version for {value.isoformat()}, found {len(matches)}"
            )
        return matches[0]

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def load_configuration(path: Path) -> TradingConfiguration:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise TradingConfigurationError(f"cannot read trading configuration: {path}") from error
    try:
        return TradingConfiguration.model_validate(payload)
    except ValueError as error:
        raise TradingConfigurationError(f"invalid trading configuration: {path}") from error
