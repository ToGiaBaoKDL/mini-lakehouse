"""Strict, effective-dated configuration for the deterministic trading core."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TradingConfigurationError(ValueError):
    """The trading configuration is invalid or has no effective version."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketConfiguration(_StrictModel):
    timezone: str
    symbols: tuple[str, ...]
    indices: tuple[str, ...]
    quote_depth: int = Field(ge=1, le=10)
    bar_interval_seconds: int = Field(ge=1)

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
        return self


class DataQualityConfiguration(_StrictModel):
    trade_stale_after_seconds: int = Field(ge=1)
    quote_stale_after_seconds: int = Field(ge=1)


class TradingVersion(_StrictModel):
    version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    effective_from: date
    effective_to: date | None
    market: MarketConfiguration
    data_quality: DataQualityConfiguration

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
