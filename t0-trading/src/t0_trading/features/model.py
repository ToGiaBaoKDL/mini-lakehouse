"""Stable point-in-time output of the deterministic feature engine."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from t0_trading.market.session import MarketSession


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WindowFeatures(_StrictModel):
    """Features calculated over one trailing receipt-time window."""

    window_seconds: int = Field(ge=1)
    trade_count: int = Field(ge=0)
    quote_change_count: int = Field(ge=0)
    trade_volume: int = Field(ge=0)
    signed_trade_volume: int
    trade_volume_per_second: Decimal | None = Field(ge=0)
    trade_volume_imbalance: Decimal | None = Field(ge=-1, le=1)
    level_one_order_flow_imbalance: int
    price_return_bps: Decimal | None
    realized_volatility_bps: Decimal | None = Field(ge=0)
    vwap: Decimal | None = Field(gt=0)
    last_price_to_vwap_bps: Decimal | None

    @model_validator(mode="after")
    def validate_window(self) -> WindowFeatures:
        if abs(self.signed_trade_volume) > self.trade_volume:
            raise ValueError("signed trade volume cannot exceed total trade volume")
        if self.quote_change_count == 0 and self.level_one_order_flow_imbalance != 0:
            raise ValueError("book flow requires at least one quote transition")
        trade_values = (
            self.trade_volume_per_second,
            self.trade_volume_imbalance,
            self.vwap,
            self.last_price_to_vwap_bps,
        )
        if self.trade_count == 0:
            if (
                self.trade_volume
                or self.signed_trade_volume
                or any(value is not None for value in trade_values)
            ):
                raise ValueError("empty trade window contains trade-derived values")
        elif self.trade_volume == 0 or any(value is None for value in trade_values):
            raise ValueError("non-empty trade window is missing trade-derived values")
        movement_values = (self.price_return_bps, self.realized_volatility_bps)
        if self.trade_count < 2 and any(value is not None for value in movement_values):
            raise ValueError("price movement features require at least two trades")
        if self.trade_count >= 2 and any(value is None for value in movement_values):
            raise ValueError("price movement features require at least two trades")
        return self


class FeatureSnapshot(_StrictModel):
    """One auditable feature vector available at an explicit decision instant."""

    schema_version: Literal[1] = 1
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    configuration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str
    trade_date: date
    decision_at: datetime
    market_session: MarketSession
    stream_session_id: str | None = Field(min_length=1)
    last_receive_sequence: int | None = Field(ge=1)
    trade_age_seconds: Decimal | None = Field(ge=0)
    quote_age_seconds: Decimal | None = Field(ge=0)
    mid_price: Decimal | None = Field(gt=0)
    microprice: Decimal | None = Field(gt=0)
    microprice_deviation_bps: Decimal | None
    spread: Decimal | None = Field(gt=0)
    spread_bps: Decimal | None = Field(gt=0)
    bid_depth: int | None = Field(gt=0)
    ask_depth: int | None = Field(gt=0)
    level_one_imbalance: Decimal | None = Field(ge=-1, le=1)
    depth_imbalance: Decimal | None = Field(ge=-1, le=1)
    windows: tuple[WindowFeatures, ...]
    reasons: tuple[str, ...]

    @field_validator("decision_at")
    @classmethod
    def normalize_decision_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_snapshot(self) -> FeatureSnapshot:
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("symbol must be uppercase")
        if (self.stream_session_id is None) != (self.last_receive_sequence is None):
            raise ValueError("stream position must be wholly present or absent")
        if tuple(sorted({window.window_seconds for window in self.windows})) != tuple(
            window.window_seconds for window in self.windows
        ):
            raise ValueError("feature windows must be unique and ascending")
        if tuple(dict.fromkeys(self.reasons)) != self.reasons:
            raise ValueError("feature reasons must be unique and stable")
        book = (
            self.mid_price,
            self.microprice,
            self.microprice_deviation_bps,
            self.spread,
            self.spread_bps,
            self.bid_depth,
            self.ask_depth,
            self.level_one_imbalance,
            self.depth_imbalance,
        )
        if any(value is None for value in book) and any(value is not None for value in book):
            raise ValueError("book features must be wholly present or absent")
        return self

    @computed_field
    @property
    def is_eligible(self) -> bool:
        return not self.reasons

    def canonical_bytes(self) -> bytes:
        """Return the stable feature payload used for immutable publication checks."""
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()
