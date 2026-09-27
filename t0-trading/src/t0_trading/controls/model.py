"""Neutral controls shared by research simulation and paper execution."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.identity import canonical_json, sha256


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


class CostPolicy(_StrictModel):
    """Explicit VND assumptions; a checked public source is not account verification."""

    version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    effective_from: date
    effective_to: date | None = None
    buy_fee_bps: Decimal = Field(ge=0, lt=10_000)
    sell_fee_bps: Decimal = Field(ge=0, lt=10_000)
    sell_tax_bps: Decimal = Field(ge=0, lt=10_000)
    extra_slippage_bps: Decimal = Field(ge=0, lt=10_000)
    basis: Literal["USER_SUPPLIED", "PUBLIC_SCHEDULE_ASSUMPTION"] = "USER_SUPPLIED"
    account_plan: str | None = None
    fee_source: str | None = None
    fee_checked_at: datetime | None = None

    @field_validator("fee_checked_at")
    @classmethod
    def normalize_checked_at(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_policy(self) -> CostPolicy:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("cost effective_to precedes effective_from")
        if (self.fee_source is None) != (self.fee_checked_at is None):
            raise ValueError("fee source and check time must be supplied together")
        if self.fee_source is not None and not self.fee_source.strip():
            raise ValueError("fee source cannot be blank")
        if self.basis == "PUBLIC_SCHEDULE_ASSUMPTION" and (
            not self.account_plan or self.fee_source is None
        ):
            raise ValueError("public fee assumptions require an account plan and source")
        if self.sell_fee_bps + self.sell_tax_bps >= 10_000:
            raise ValueError("sell charges must be less than sale notional")
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


class AdvancePolicy(_StrictModel):
    """Opt-in sale-proceeds advance; absent means pending sales are not spendable."""

    settlement_date: date
    daily_interest_bps: Decimal = Field(ge=0, lt=10_000)
    source: str = Field(min_length=1)


class SelectionEvidence(_StrictModel):
    """Evidence that an arbitration was recorded before entry planning."""

    arbitration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_at: datetime
    source: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("selected_at")
    @classmethod
    def normalize_selected_at(cls, value: datetime) -> datetime:
        return _utc(value)


class RiskLimits(_StrictModel):
    """Explicit operational limits, never approval to trade capital."""

    max_cycle_quantity: int = Field(ge=1)
    max_order_notional_vnd: Decimal = Field(gt=0)
    max_cycles_per_day: int = Field(ge=1)
    max_daily_loss_vnd: Decimal = Field(gt=0)
    cash_reserve_vnd: Decimal = Field(ge=0)


class AccountPosition(_StrictModel):
    symbol: str
    settled_qty: int = Field(ge=0)
    t1_qty: int = Field(ge=0)
    t2_qty: int = Field(ge=0)
    core_min_qty: int = Field(ge=0)
    start_price: Decimal = Field(gt=0)

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        if not value or value != value.strip().upper():
            raise ValueError("symbol must be uppercase")
        return value

    @model_validator(mode="after")
    def validate_core(self) -> AccountPosition:
        if self.core_min_qty > self.settled_qty:
            raise ValueError("core quantity cannot exceed settled quantity")
        return self

    @property
    def total_qty(self) -> int:
        return self.settled_qty + self.t1_qty + self.t2_qty


class AccountSnapshot(_StrictModel):
    as_of: datetime
    source: str = Field(min_length=1)
    cash_vnd: Decimal = Field(
        ge=0,
        description="Settled cash, excluding sale receivables and buying-power loans.",
    )
    positions: tuple[AccountPosition, ...]

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_positions(self) -> AccountSnapshot:
        if not self.positions or len({item.symbol for item in self.positions}) != len(
            self.positions
        ):
            raise ValueError("account positions must contain unique symbols")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())
