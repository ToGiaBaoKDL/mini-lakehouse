"""Stable research scores derived only from point-in-time feature snapshots."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from t0_trading.identity import canonical_json, sha256
from t0_trading.outcomes import Action

StrategyName = Literal["momentum", "order_flow", "relative_value"]
STRATEGY_NAMES: tuple[StrategyName, ...] = ("momentum", "order_flow", "relative_value")


class StrategyScore(BaseModel):
    """One versioned directional research score for one feature snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    strategy: StrategyName
    strategy_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    strategy_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    feature_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    peer_feature_snapshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    symbol: str
    trade_date: date
    decision_at: datetime
    signed_score: Decimal = Field(ge=-1, le=1)

    @field_validator("decision_at")
    @classmethod
    def normalize_decision_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_score(self) -> StrategyScore:
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("symbol must be uppercase")
        has_peer = self.peer_feature_snapshot_sha256 is not None
        if has_peer != (self.strategy == "relative_value"):
            raise ValueError("only relative-value scores require peer snapshot lineage")
        if self.peer_feature_snapshot_sha256 == self.feature_snapshot_sha256:
            raise ValueError("relative-value peer must differ from the target snapshot")
        return self

    @computed_field
    @property
    def direction(self) -> Action | None:
        """Conditional outcome direction; this is not an executable action."""
        if self.signed_score > 0:
            return "BUY"
        if self.signed_score < 0:
            return "SELL"
        return None

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())
