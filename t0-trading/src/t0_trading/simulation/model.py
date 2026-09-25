"""Strict inputs and auditable outputs for offline T0 cycle simulation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.decisions import StrategyDecision
from t0_trading.identity import canonical_json, sha256
from t0_trading.outcomes import OutcomeLabel

_MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


class CostPolicy(_StrictModel):
    """Explicit VND research assumptions; a checked public source is not account verification."""

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


class AdvancePolicy(_StrictModel):
    """Opt-in sale-proceeds advance; no policy means pending sales are not spendable."""

    settlement_date: date
    daily_interest_bps: Decimal = Field(ge=0, lt=10_000)
    source: str = Field(min_length=1)


class SelectionRecord(_StrictModel):
    """A decision-only selection recorded before its conditional outcome entry."""

    decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    priority: int = Field(ge=0)
    selected_at: datetime
    source: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("selected_at")
    @classmethod
    def normalize_selected_at(cls, value: datetime) -> datetime:
        return _utc(value)


class RiskLimits(_StrictModel):
    """Scenario limits, not approval to emit an advisory or live signal."""

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


class ClosingMark(_StrictModel):
    symbol: str
    as_of: datetime
    price: Decimal = Field(gt=0)
    source: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return _utc(value)


class CycleProposal(_StrictModel):
    """One explicitly selected decision and its exact conditional outcome."""

    priority: int = Field(ge=0)
    decision: StrategyDecision
    outcome: OutcomeLabel

    @model_validator(mode="after")
    def validate_lineage(self) -> CycleProposal:
        decision, outcome = self.decision, self.outcome
        if decision.action == "ABSTAIN":
            raise ValueError("cycle proposal requires an actionable decision")
        if (
            decision.outcome_version != outcome.outcome_version
            or decision.outcome_configuration_sha256 != outcome.outcome_configuration_sha256
            or decision.feature_version != outcome.feature_version
            or decision.feature_configuration_sha256 != outcome.feature_configuration_sha256
            or decision.feature_snapshot_sha256 != outcome.feature_snapshot_sha256
            or decision.symbol != outcome.symbol
            or decision.trade_date != outcome.trade_date
            or decision.decision_at != outcome.decision_at
            or decision.action != outcome.action
            or decision.horizon_seconds != outcome.horizon_seconds
        ):
            raise ValueError("cycle proposal decision and outcome lineage do not match")
        if outcome.reasons and outcome.entry_vwap is not None and outcome.horizon_vwap is not None:
            raise ValueError("actionable decision cannot use an ineligible priced outcome")
        return self


class SimulationRequest(_StrictModel):
    """One exchange-local day; proposals must already be strategy-arbitrated."""

    schema_version: Literal[1] = 1
    trade_date: date
    account: AccountSnapshot
    costs: CostPolicy
    advance: AdvancePolicy | None = None
    risk: RiskLimits
    lot_size: int = Field(ge=1)
    closing_marks: tuple[ClosingMark, ...]
    selection_records: tuple[SelectionRecord, ...]
    proposals: tuple[CycleProposal, ...]

    @model_validator(mode="before")
    @classmethod
    def accept_serialized_outcomes(cls, value: object) -> object:
        """Accept OutcomeLabel's redundant computed field in its default JSON dump."""
        if not isinstance(value, dict) or not isinstance(value.get("proposals"), list):
            return value
        cleaned = []
        for proposal in value["proposals"]:
            if not isinstance(proposal, dict) or not isinstance(proposal.get("outcome"), dict):
                cleaned.append(proposal)
                continue
            outcome = proposal["outcome"]
            if "is_eligible" not in outcome:
                cleaned.append(proposal)
                continue
            if outcome["is_eligible"] is not (not outcome.get("reasons")):
                raise ValueError("serialized outcome eligibility conflicts with its reasons")
            cleaned.append(
                {**proposal, "outcome": {k: v for k, v in outcome.items() if k != "is_eligible"}}
            )
        return {**value, "proposals": cleaned}

    @model_validator(mode="after")
    def validate_request(self) -> SimulationRequest:
        if not self.costs.contains(self.trade_date):
            raise ValueError("cost policy is not effective for trade date")
        if self.advance is not None and self.advance.settlement_date <= self.trade_date:
            raise ValueError("advance settlement date must follow the trade date")
        if self.account.as_of.astimezone(_MARKET_TIMEZONE).date() != self.trade_date:
            raise ValueError("account snapshot must be from the trade date")
        symbols = {item.symbol for item in self.account.positions}
        marks = {item.symbol: item for item in self.closing_marks}
        if len(marks) != len(self.closing_marks) or set(marks) != symbols:
            raise ValueError("closing marks must cover account symbols exactly once")
        if any(
            mark.as_of.astimezone(_MARKET_TIMEZONE).date() != self.trade_date
            or mark.as_of <= self.account.as_of
            for mark in self.closing_marks
        ):
            raise ValueError("closing marks must follow the same-day account snapshot")
        decisions = [proposal.decision for proposal in self.proposals]
        if len({decision.sha256 for decision in decisions}) != len(decisions) or len(
            {(decision.symbol, decision.decision_at) for decision in decisions}
        ) != len(decisions):
            raise ValueError("proposals must have unique decisions and symbol clocks")
        if len({(item.outcome.entry_at, item.priority) for item in self.proposals}) != len(
            self.proposals
        ):
            raise ValueError("simultaneous proposals require unique explicit priorities")
        selections = {item.decision_sha256: item for item in self.selection_records}
        if len(selections) != len(self.selection_records) or set(selections) != {
            decision.sha256 for decision in decisions
        }:
            raise ValueError("selection records must cover selected decisions exactly once")
        if any(
            (selection := selections[proposal.decision.sha256]).priority != proposal.priority
            or not (
                proposal.decision.decision_at <= selection.selected_at < proposal.outcome.entry_at
            )
            for proposal in self.proposals
        ):
            raise ValueError("selection must match priority and precede outcome entry")
        if any(
            decision.trade_date != self.trade_date
            or decision.symbol not in symbols
            or decision.decision_at <= self.account.as_of
            or proposal.outcome.entry_at >= marks[decision.symbol].as_of
            or proposal.outcome.horizon_at > marks[decision.symbol].as_of
            for proposal in self.proposals
            for decision in (proposal.decision,)
        ):
            raise ValueError("proposal or outcome falls outside account and closing-mark window")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class CycleResult(_StrictModel):
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str
    strategy: str
    action: Literal["BUY", "SELL"]
    quantity: int = Field(ge=1)
    entry_at: datetime
    horizon_at: datetime
    status: Literal["CLOSED", "REJECTED", "OPEN"]
    reason: str | None
    entry_price: Decimal | None
    exit_price: Decimal | None
    gross_pnl_vnd: Decimal | None
    trading_cost_vnd: Decimal | None
    net_pnl_vnd: Decimal | None


class EndPosition(_StrictModel):
    symbol: str
    settled_qty: int = Field(ge=0)
    t1_qty: int = Field(ge=0)
    t2_qty: int = Field(ge=0)
    bought_today_qty: int = Field(ge=0)


class SimulationReport(_StrictModel):
    """Complete means reconstructable, never approved or profitable."""

    schema_version: Literal[1] = 1
    trade_date: date
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fee_provenance_recorded: bool
    fee_basis: Literal["USER_SUPPLIED", "PUBLIC_SCHEDULE_ASSUMPTION"]
    account_plan: str | None
    selection_reference_recorded: bool
    status: Literal["COMPLETE", "INCOMPLETE"]
    cycles: tuple[CycleResult, ...]
    starting_cash_vnd: Decimal
    ending_cash_vnd: Decimal
    pending_sale_proceeds_vnd: Decimal
    advance_principal_vnd: Decimal
    advance_interest_vnd: Decimal
    end_positions: tuple[EndPosition, ...]
    hold_pnl_vnd: Decimal
    gross_t0_alpha_vnd: Decimal | None
    trading_cost_vnd: Decimal | None
    financing_cost_vnd: Decimal | None
    net_t0_alpha_vnd: Decimal | None
    hold_plus_t0_pnl_vnd: Decimal | None

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())
