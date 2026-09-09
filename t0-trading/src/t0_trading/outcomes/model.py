"""Durable contract for deterministic conditional execution markouts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from t0_trading.identity import canonical_json, sha256
from t0_trading.numeric import basis_points

Action = Literal["BUY", "SELL"]


class OutcomeLabel(BaseModel):
    """One gross Top-3 markout conditional on an action at a feature snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    outcome_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    feature_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stream_session_id: str = Field(min_length=1)
    symbol: str
    trade_date: date
    decision_at: datetime
    action: Action
    horizon_seconds: int = Field(ge=1)
    order_quantity: int = Field(ge=1)
    entry_at: datetime
    horizon_at: datetime
    entry_quote_received_at: datetime | None
    entry_receive_sequence: int | None = Field(ge=1)
    entry_vwap: Decimal | None = Field(gt=0)
    horizon_quote_received_at: datetime | None
    horizon_receive_sequence: int | None = Field(ge=1)
    horizon_vwap: Decimal | None = Field(gt=0)
    gross_return_bps: Decimal | None
    reasons: tuple[str, ...]

    @field_validator(
        "decision_at",
        "entry_at",
        "horizon_at",
        "entry_quote_received_at",
        "horizon_quote_received_at",
    )
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("outcome timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_label(self) -> OutcomeLabel:
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("symbol must be uppercase")
        if not self.decision_at <= self.entry_at < self.horizon_at:
            raise ValueError("outcome timestamps are not ordered")
        if self.horizon_at - self.decision_at != timedelta(seconds=self.horizon_seconds):
            raise ValueError("horizon_seconds does not match horizon_at")
        for received_at, sequence, price, evaluated_at in (
            (
                self.entry_quote_received_at,
                self.entry_receive_sequence,
                self.entry_vwap,
                self.entry_at,
            ),
            (
                self.horizon_quote_received_at,
                self.horizon_receive_sequence,
                self.horizon_vwap,
                self.horizon_at,
            ),
        ):
            if (received_at is None) != (sequence is None):
                raise ValueError("quote position must be wholly present or absent")
            if price is not None and received_at is None:
                raise ValueError("simulated price requires quote lineage")
            if received_at is not None and received_at > evaluated_at:
                raise ValueError("outcome uses a quote unavailable at its evaluation time")
        entry_vwap = self.entry_vwap
        horizon_vwap = self.horizon_vwap
        prices_present = entry_vwap is not None and horizon_vwap is not None
        if prices_present != (self.gross_return_bps is not None):
            raise ValueError("gross return requires both simulated prices")
        if not self.reasons and not prices_present:
            raise ValueError("eligible outcome requires both simulated prices")
        if entry_vwap is not None and horizon_vwap is not None:
            movement = (
                horizon_vwap - entry_vwap if self.action == "BUY" else entry_vwap - horizon_vwap
            )
            if self.gross_return_bps != basis_points(movement, entry_vwap):
                raise ValueError("gross return does not reconcile with simulated prices")
        if tuple(dict.fromkeys(self.reasons)) != self.reasons:
            raise ValueError("outcome reasons must be unique and stable")
        return self

    @computed_field
    @property
    def is_eligible(self) -> bool:
        return not self.reasons

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())
