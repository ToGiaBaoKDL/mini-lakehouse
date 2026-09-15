"""Immutable decision journal record shared by offline and shadow paths."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.configuration import StrategyName
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.session import MarketSession

DecisionAction = Literal["BUY", "SELL", "ABSTAIN"]


class StrategyDecision(BaseModel):
    """One explainable strategy decision at an exact point-in-time feature boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    decision_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    decision_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy: StrategyName
    strategy_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    strategy_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    feature_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_score_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    symbol: str
    trade_date: date
    decision_at: datetime
    market_session: MarketSession
    horizon_seconds: int = Field(ge=1)
    signed_score: Decimal | None = Field(default=None, ge=-1, le=1)
    minimum_strength: Decimal | None = Field(default=None, gt=0, le=1)
    action: DecisionAction
    reasons: tuple[str, ...]

    @field_validator("decision_at")
    @classmethod
    def normalize_decision_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_decision(self) -> StrategyDecision:
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("symbol must be uppercase")
        if tuple(dict.fromkeys(self.reasons)) != self.reasons:
            raise ValueError("decision reasons must be unique and stable")
        score_present = self.strategy_score_sha256 is not None
        if score_present != (self.signed_score is not None):
            raise ValueError("decision score value and lineage must be wholly present or absent")
        directed = self.signed_score is not None and self.signed_score != 0
        if directed != (self.minimum_strength is not None):
            raise ValueError("directed scores require the applied directional threshold")
        if (self.action == "ABSTAIN") != bool(self.reasons):
            raise ValueError("only abstentions may carry reasons")
        if self.action != "ABSTAIN":
            if self.signed_score is None or self.minimum_strength is None:
                raise ValueError("actionable decision requires a directed score")
            if abs(self.signed_score) < self.minimum_strength:
                raise ValueError("actionable decision does not meet its threshold")
            if (self.action == "BUY") != (self.signed_score > 0):
                raise ValueError("decision action conflicts with score direction")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())
