"""Immutable context contract for research decisions and replay."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.identity import canonical_json, sha256

MarketRegime = Literal["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOLATILITY", "UNKNOWN"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ZoneContext(_StrictModel):
    symbol: str
    current_feature_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    history_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    observation_count: int = Field(ge=0)
    support: Decimal | None = Field(default=None, gt=0)
    resistance: Decimal | None = Field(default=None, gt=0)
    support_distance_bps: Decimal | None
    resistance_distance_bps: Decimal | None
    near_support_strength: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_zone(self) -> ZoneContext:
        values = (
            self.history_sha256,
            self.support,
            self.resistance,
            self.support_distance_bps,
            self.resistance_distance_bps,
            self.near_support_strength,
        )
        if any(value is None for value in values) != all(value is None for value in values):
            raise ValueError("zone values must be wholly present or absent")
        if (
            self.support is not None
            and self.resistance is not None
            and self.support > self.resistance
        ):
            raise ValueError("zone support cannot exceed resistance")
        return self

    @property
    def is_available(self) -> bool:
        return self.support is not None


class MarketWindowContext(_StrictModel):
    window_seconds: int = Field(ge=1)
    return_bps: Decimal
    realized_volatility_bps: Decimal = Field(ge=0)


class IndexContext(_StrictModel):
    index: str
    value: Decimal | None = Field(default=None, gt=0)
    age_seconds: Decimal | None = Field(default=None, ge=0)
    stream_session_id: str | None = Field(default=None, min_length=1)
    receive_sequence: int | None = Field(default=None, ge=1)
    windows: tuple[MarketWindowContext, ...]
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_index(self) -> IndexContext:
        if self.index != self.index.strip().upper():
            raise ValueError("index identifier must be uppercase")
        lineage = (self.value, self.age_seconds, self.stream_session_id, self.receive_sequence)
        if any(value is None for value in lineage) and any(value is not None for value in lineage):
            raise ValueError("index value lineage must be wholly present or absent")
        if tuple(window.window_seconds for window in self.windows) != tuple(
            sorted({window.window_seconds for window in self.windows})
        ):
            raise ValueError("index windows must be unique and ascending")
        if tuple(dict.fromkeys(self.reasons)) != self.reasons:
            raise ValueError("index reasons must be unique")
        return self

    @property
    def is_eligible(self) -> bool:
        return not self.reasons


class DecisionContext(_StrictModel):
    """Context known at one decision clock; never reconstructed from future outcomes."""

    schema_version: Literal[1] = 1
    context_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    context_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trade_date: date
    decision_at: datetime
    zones: tuple[ZoneContext, ...]
    indices: tuple[IndexContext, ...]
    market_confirmation_strength: Decimal | None = Field(default=None, ge=0, le=1)
    regime: MarketRegime
    reasons: tuple[str, ...]

    @field_validator("decision_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("context decision_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_context(self) -> DecisionContext:
        if tuple(zone.symbol for zone in self.zones) != tuple(
            sorted({zone.symbol for zone in self.zones})
        ):
            raise ValueError("context zones must be unique and sorted")
        if tuple(item.index for item in self.indices) != tuple(
            sorted({item.index for item in self.indices})
        ):
            raise ValueError("context indices must be unique and sorted")
        if tuple(dict.fromkeys(self.reasons)) != self.reasons:
            raise ValueError("context reasons must be unique")
        if self.regime == "UNKNOWN" and not self.reasons:
            raise ValueError("unknown regime requires an explicit reason")
        if self.regime != "UNKNOWN" and self.reasons:
            raise ValueError("classified regime cannot carry context failures")
        if (self.market_confirmation_strength is None) != bool(self.reasons):
            raise ValueError("market confirmation availability must match context health")
        return self

    def zone(self, symbol: str) -> ZoneContext:
        matches = tuple(zone for zone in self.zones if zone.symbol == symbol)
        if len(matches) != 1:
            raise ValueError(f"context requires one zone for {symbol}")
        return matches[0]

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())
