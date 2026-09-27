"""Strict inputs and auditable outputs for offline T0 cycle simulation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.arbitration import CandidateArbitration, require_selected_candidate
from t0_trading.controls import (
    AccountSnapshot,
    AdvancePolicy,
    CostPolicy,
    RiskLimits,
    SelectionEvidence,
)
from t0_trading.identity import canonical_json, sha256
from t0_trading.outcomes import OutcomeLabel
from t0_trading.strategy.baselines import BaselineCandidate

_MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


class ClosingMark(_StrictModel):
    symbol: str
    as_of: datetime
    price: Decimal = Field(gt=0)
    source: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return _utc(value)


class ArbitratedCycleProposal(_StrictModel):
    """One selected buy-first candidate and its exact conditional outcome."""

    candidate: BaselineCandidate
    arbitration: CandidateArbitration
    outcome: OutcomeLabel

    @model_validator(mode="after")
    def validate_lineage(self) -> ArbitratedCycleProposal:
        candidate, arbitration, outcome = self.candidate, self.arbitration, self.outcome
        require_selected_candidate(candidate, arbitration)
        if (
            outcome.feature_snapshot_sha256 != candidate.feature_snapshot_sha256
            or outcome.symbol != candidate.symbol
            or outcome.trade_date != candidate.trade_date
            or outcome.decision_at != candidate.decision_at
            or outcome.action != arbitration.action
            or outcome.horizon_seconds != arbitration.horizon_seconds
        ):
            raise ValueError("cycle proposal arbitration and outcome lineage do not match")
        if outcome.reasons and outcome.entry_vwap is not None and outcome.horizon_vwap is not None:
            raise ValueError("selected candidate cannot use an ineligible priced outcome")
        return self

    @property
    def priority(self) -> int:
        if self.arbitration.priority is None:
            raise ValueError("selected arbitration is missing priority")
        return self.arbitration.priority

    @property
    def cycle_id(self) -> str:
        return self.arbitration.sha256


class SimulationRequest(_StrictModel):
    """One exchange-local day of explicitly arbitrated buy-first proposals."""

    schema_version: Literal[2] = 2
    trade_date: date
    account: AccountSnapshot
    costs: CostPolicy
    advance: AdvancePolicy | None = None
    risk: RiskLimits
    lot_size: int = Field(ge=1)
    closing_marks: tuple[ClosingMark, ...]
    selection_evidence: tuple[SelectionEvidence, ...]
    proposals: tuple[ArbitratedCycleProposal, ...]

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
        arbitrations = [proposal.arbitration for proposal in self.proposals]
        if len({item.sha256 for item in arbitrations}) != len(arbitrations) or len(
            {(item.symbol, item.decision_at) for item in arbitrations}
        ) != len(arbitrations):
            raise ValueError("proposals must have unique arbitrations and symbol clocks")
        if len({(item.outcome.entry_at, item.priority) for item in self.proposals}) != len(
            self.proposals
        ):
            raise ValueError("simultaneous proposals require unique explicit priorities")
        selections = {item.arbitration_sha256: item for item in self.selection_evidence}
        if len(selections) != len(self.selection_evidence) or set(selections) != {
            item.sha256 for item in arbitrations
        }:
            raise ValueError("selection evidence must cover selected arbitrations exactly once")
        if any(
            not (
                proposal.candidate.decision_at
                <= selections[proposal.arbitration.sha256].selected_at
                < proposal.outcome.entry_at
            )
            for proposal in self.proposals
        ):
            raise ValueError("selection evidence must precede outcome entry")
        if any(
            proposal.candidate.trade_date != self.trade_date
            or proposal.candidate.symbol not in symbols
            or proposal.candidate.decision_at <= self.account.as_of
            or proposal.outcome.entry_at >= marks[proposal.candidate.symbol].as_of
            or proposal.outcome.horizon_at > marks[proposal.candidate.symbol].as_of
            for proposal in self.proposals
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
    action: Literal["BUY"]
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

    schema_version: Literal[2] = 2
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
