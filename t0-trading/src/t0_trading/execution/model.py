"""Broker-neutral contracts for causal paper order planning and lifecycle evidence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.arbitration import CandidateArbitration, require_selected_candidate
from t0_trading.controls import AccountSnapshot, CostPolicy, RiskLimits, SelectionEvidence
from t0_trading.identity import canonical_json, sha256
from t0_trading.promotion import PromotionGateReport
from t0_trading.strategy.baselines import BaselineCandidate

_MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

PaperBlockReason = Literal[
    "MARKET_STATUS_INELIGIBLE",
    "STALE_QUOTE",
    "INSUFFICIENT_ASK_QUANTITY",
    "INSUFFICIENT_BID_QUANTITY",
    "PRICE_LIMIT",
    "ENTRY_NOT_TERMINAL",
    "ENTRY_UNFILLED",
    "BEFORE_EXIT_HORIZON",
    "POSITION_UNAVAILABLE",
    "INSUFFICIENT_SETTLED_ABOVE_CORE",
    "QUANTITY_LIMIT",
    "ORDER_NOTIONAL_LIMIT",
    "CYCLE_LIMIT",
    "DAILY_LOSS_LIMIT",
    "CASH_LIMIT",
]
PaperEventType = Literal[
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "REJECTED",
    "CANCELLED",
    "EXPIRED",
]
PaperOrderStatus = Literal[
    "CREATED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "REJECTED",
    "CANCELLED",
    "EXPIRED",
]
PaperReservationStatus = Literal["ENTRY_PENDING", "OPEN", "EXIT_PENDING"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("paper execution timestamps must be timezone-aware")
    return value.astimezone(UTC)


class PaperMarketSnapshot(_StrictModel):
    """Exact market facts visible when a paper order is planned."""

    symbol: str
    observed_at: datetime
    quote_received_at: datetime
    stream_session_id: str = Field(min_length=1)
    receive_sequence: int = Field(ge=1)
    best_ask_price: Decimal = Field(gt=0)
    best_ask_quantity: int = Field(ge=1)
    best_bid_price: Decimal = Field(gt=0)
    best_bid_quantity: int = Field(ge=1)
    reference_price: Decimal = Field(gt=0)
    ceiling_price: Decimal = Field(gt=0)
    floor_price: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    market_status: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("observed_at", "quote_received_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_validator("symbol", "market_status")
    @classmethod
    def uppercase_identifier(cls, value: str) -> str:
        if not value or value != value.strip().upper():
            raise ValueError("paper market identifiers must be uppercase")
        return value

    @model_validator(mode="after")
    def validate_market(self) -> PaperMarketSnapshot:
        if self.quote_received_at > self.observed_at:
            raise ValueError("paper quote cannot be observed before receipt")
        if not self.floor_price <= self.reference_price <= self.ceiling_price:
            raise ValueError("paper security price band is inconsistent")
        if not self.floor_price <= self.best_ask_price <= self.ceiling_price:
            raise ValueError("paper ask price is outside the security price band")
        if not self.floor_price <= self.best_bid_price < self.best_ask_price:
            raise ValueError("paper bid/ask prices are crossed or outside the security price band")
        if self.best_ask_price % self.tick_size or self.best_bid_price % self.tick_size:
            raise ValueError("paper quote prices must align to their current tick size")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class PaperResourcePosition(_StrictModel):
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]*$")
    available_exit_quantity: int = Field(ge=0)


class PaperReservation(_StrictModel):
    entry_intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    exit_intent_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]*$")
    status: PaperReservationStatus
    reserved_cash_vnd: Decimal = Field(ge=0)
    reserved_exit_quantity: int = Field(ge=0)
    entry_filled_quantity: int = Field(ge=0)
    entry_average_fill_price: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_reservation(self) -> PaperReservation:
        if (self.entry_filled_quantity == 0) != (self.entry_average_fill_price is None):
            raise ValueError("paper reservation entry fill is inconsistent")
        if self.status == "ENTRY_PENDING" and (
            self.exit_intent_sha256 is not None
            or self.reserved_cash_vnd <= 0
            or self.entry_filled_quantity != 0
        ):
            raise ValueError("pending paper entry reservation is inconsistent")
        if self.status == "OPEN" and (
            self.exit_intent_sha256 is not None
            or self.reserved_cash_vnd != 0
            or self.entry_filled_quantity == 0
            or self.reserved_exit_quantity != self.entry_filled_quantity
        ):
            raise ValueError("open paper reservation is inconsistent")
        if self.status == "EXIT_PENDING" and (
            self.exit_intent_sha256 is None
            or self.reserved_cash_vnd != 0
            or self.entry_filled_quantity == 0
            or self.reserved_exit_quantity != self.entry_filled_quantity
        ):
            raise ValueError("pending paper exit reservation is inconsistent")
        return self


class PaperResourceLedger(_StrictModel):
    """CAS-friendly paper resources; persistence must condition on the prior hash."""

    schema_version: Literal[1] = 1
    trade_date: date
    account_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=0)
    available_cash_vnd: Decimal = Field(ge=0)
    positions: tuple[PaperResourcePosition, ...]
    started_cycles: int = Field(ge=0)
    realized_net_pnl_vnd: Decimal
    reservations: tuple[PaperReservation, ...]

    @model_validator(mode="after")
    def validate_ledger(self) -> PaperResourceLedger:
        symbols = tuple(item.symbol for item in self.positions)
        reservation_ids = tuple(item.entry_intent_sha256 for item in self.reservations)
        if tuple(sorted(set(symbols))) != symbols:
            raise ValueError("paper resource positions must be unique and ordered")
        if tuple(sorted(set(reservation_ids))) != reservation_ids:
            raise ValueError("paper reservations must be unique and ordered")
        if any(item.symbol not in symbols for item in self.reservations):
            raise ValueError("paper reservation symbol is absent from account resources")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class PaperExecutionRequest(_StrictModel):
    """One selected candidate plus only facts available before paper submission."""

    candidate: BaselineCandidate
    arbitration: CandidateArbitration
    promotion: PromotionGateReport
    selection: SelectionEvidence
    market: PaperMarketSnapshot
    account: AccountSnapshot
    costs: CostPolicy
    risk: RiskLimits
    resources: PaperResourceLedger

    @model_validator(mode="after")
    def validate_request(self) -> PaperExecutionRequest:
        require_selected_candidate(self.candidate, self.arbitration)
        promoted = tuple(
            item
            for item in self.promotion.targets
            if (item.strategy, item.symbol, item.horizon_seconds)
            == (
                self.candidate.strategy,
                self.candidate.symbol,
                self.arbitration.horizon_seconds,
            )
        )
        if (
            self.promotion.status != "PASS"
            or self.promotion.arbitration_version != self.arbitration.arbitration_version
            or self.promotion.arbitration_configuration_sha256
            != self.arbitration.arbitration_configuration_sha256
            or len(promoted) != 1
            or promoted[0].status != "PASS"
            or not self.promotion.holdout_dates
            or self.promotion.holdout_dates[-1] >= self.candidate.trade_date
        ):
            raise ValueError("paper candidate lacks prior matching promotion evidence")
        if (
            self.selection.arbitration_sha256 != self.arbitration.sha256
            or self.selection.selected_at < self.candidate.decision_at
            or self.selection.selected_at > self.market.observed_at
        ):
            raise ValueError("paper selection evidence does not match arbitration timing")
        if (
            self.market.symbol != self.candidate.symbol
            or self.market.observed_at < self.candidate.decision_at
            or self.market.observed_at.astimezone(_MARKET_TIMEZONE).date()
            != self.candidate.trade_date
        ):
            raise ValueError("paper market snapshot does not match candidate lineage")
        if (
            self.account.as_of > self.market.observed_at
            or self.account.as_of.astimezone(_MARKET_TIMEZONE).date()
            != self.candidate.trade_date
        ):
            raise ValueError("paper account snapshot does not precede same-day planning")
        if not self.costs.contains(self.candidate.trade_date):
            raise ValueError("paper cost policy is not effective for candidate date")
        if (
            self.resources.trade_date != self.candidate.trade_date
            or self.resources.account_snapshot_sha256 != self.account.sha256
        ):
            raise ValueError("paper resource ledger does not match account snapshot")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class PaperOrderIntent(_StrictModel):
    """Deterministic paper-only order; never a broker-routing payload."""

    schema_version: Literal[1] = 1
    mode: Literal["PAPER"] = "PAPER"
    execution_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    execution_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    promotion_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arbitration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]*$")
    trade_date: date
    decision_at: datetime
    horizon_seconds: int = Field(ge=1, le=86_400)
    leg: Literal["ENTRY", "EXIT"]
    action: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT"] = "LIMIT"
    quantity: int = Field(ge=1)
    limit_price: Decimal = Field(gt=0)
    priority: int = Field(ge=0)
    created_at: datetime
    expires_at: datetime
    reserved_cash_vnd: Decimal = Field(ge=0)
    reserved_exit_quantity: int = Field(ge=1)
    market_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_intent_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("decision_at", "created_at", "expires_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_intent(self) -> PaperOrderIntent:
        if not self.decision_at <= self.created_at < self.expires_at:
            raise ValueError("paper intent timestamps are not ordered")
        if self.reserved_exit_quantity != self.quantity:
            raise ValueError("paper intent exit reservation must equal order quantity")
        if self.leg == "ENTRY" and (
            self.action != "BUY"
            or self.parent_intent_sha256 is not None
            or self.reserved_cash_vnd <= 0
        ):
            raise ValueError("paper entry intent is inconsistent")
        if self.leg == "EXIT" and (
            self.action != "SELL"
            or self.parent_intent_sha256 is None
            or self.reserved_cash_vnd != 0
        ):
            raise ValueError("paper exit intent is inconsistent")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class PaperOrderPlan(_StrictModel):
    arbitration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["READY", "BLOCKED"]
    reasons: tuple[PaperBlockReason, ...]
    intent: PaperOrderIntent | None

    @model_validator(mode="after")
    def validate_plan(self) -> PaperOrderPlan:
        if tuple(dict.fromkeys(self.reasons)) != self.reasons:
            raise ValueError("paper block reasons must be unique and ordered")
        if (self.status == "READY") != (self.intent is not None and not self.reasons):
            raise ValueError("paper order plan is inconsistent")
        return self


class PaperFillEvidence(_StrictModel):
    """One fill increment tied to immutable market or broker-paper evidence."""

    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]*$")
    action: Literal["BUY", "SELL"]
    observed_at: datetime
    stream_session_id: str = Field(min_length=1)
    receive_sequence: int = Field(ge=1)
    filled_quantity: int = Field(ge=1)
    available_quantity: int = Field(ge=1)
    fill_price: Decimal = Field(gt=0)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_liquidity(self) -> PaperFillEvidence:
        if self.filled_quantity > self.available_quantity:
            raise ValueError("paper fill cannot exceed evidenced liquidity")
        return self


class PaperOrderEvent(_StrictModel):
    """Append-only observation from a paper adapter."""

    schema_version: Literal[1] = 1
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence: int = Field(ge=1)
    event_type: PaperEventType
    occurred_at: datetime
    cumulative_filled_quantity: int = Field(ge=0)
    average_fill_price: Decimal | None = Field(default=None, gt=0)
    reason: str | None = None
    adapter_event_id: str = Field(min_length=1)
    adapter_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fill_evidence: PaperFillEvidence | None = None

    @field_validator("occurred_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_event(self) -> PaperOrderEvent:
        if (self.cumulative_filled_quantity == 0) != (self.average_fill_price is None):
            raise ValueError("paper fill quantity and average price must appear together")
        if self.event_type in {"REJECTED", "CANCELLED", "EXPIRED"}:
            if self.reason is None or not self.reason.strip():
                raise ValueError("terminal paper event requires a reason")
        elif self.reason is not None:
            raise ValueError("non-terminal paper event cannot carry a reason")
        if (self.event_type in {"PARTIALLY_FILLED", "FILLED"}) != (
            self.fill_evidence is not None
        ):
            raise ValueError("paper fill events require exact market evidence")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class PaperOrderState(_StrictModel):
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: PaperOrderStatus
    cumulative_filled_quantity: int = Field(ge=0)
    average_fill_price: Decimal | None = Field(default=None, gt=0)
    event_count: int = Field(ge=0)
    last_event_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_state(self) -> PaperOrderState:
        if (self.cumulative_filled_quantity == 0) != (self.average_fill_price is None):
            raise ValueError("paper order state fill quantity and price are inconsistent")
        if self.event_count == 0 and (
            self.status != "CREATED" or self.last_event_sha256 is not None
        ):
            raise ValueError("paper order state without events must remain CREATED")
        if self.event_count > 0 and (
            self.status == "CREATED" or self.last_event_sha256 is None
        ):
            raise ValueError("paper order state with events requires event lineage")
        return self


class PaperExitRequest(_StrictModel):
    """A paper exit for the quantity actually filled by one terminal entry order."""

    entry_intent: PaperOrderIntent
    entry_state: PaperOrderState
    market: PaperMarketSnapshot
    resources: PaperResourceLedger

    @model_validator(mode="after")
    def validate_request(self) -> PaperExitRequest:
        if (
            self.entry_intent.leg != "ENTRY"
            or self.entry_state.intent_sha256 != self.entry_intent.sha256
            or self.market.symbol != self.entry_intent.symbol
            or self.market.observed_at.astimezone(_MARKET_TIMEZONE).date()
            != self.entry_intent.trade_date
            or self.entry_state.cumulative_filled_quantity > self.entry_intent.quantity
            or (
                self.entry_state.status == "FILLED"
                and self.entry_state.cumulative_filled_quantity != self.entry_intent.quantity
            )
            or not any(
                item.entry_intent_sha256 == self.entry_intent.sha256
                and item.status == "OPEN"
                and 0 < item.reserved_exit_quantity
                <= self.entry_state.cumulative_filled_quantity
                for item in self.resources.reservations
            )
        ):
            raise ValueError("paper exit does not match its entry lineage")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())
