"""Immutable outputs from point-in-time candidate arbitration."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.identity import canonical_json, sha256
from t0_trading.strategy.baselines import BaselineCandidate

ArbitrationStatus = Literal["SELECTED", "REJECTED"]
RejectionReason = Literal[
    "UPSTREAM_BLOCKED",
    "BELOW_MINIMUM_STRENGTH",
    "LOWER_STRATEGY_PRIORITY",
    "SYMBOL_COOLDOWN",
    "CLOCK_CAPACITY",
]


class CandidateArbitration(BaseModel):
    """One policy decision for one immutable strategy candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    arbitration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    symbol: str
    trade_date: date
    decision_at: datetime
    action: Literal["BUY"] = "BUY"
    horizon_seconds: int = Field(ge=1, le=86_400)
    strength: Decimal = Field(ge=0, le=1)
    status: ArbitrationStatus
    rejection_reason: RejectionReason | None = None
    priority: int | None = Field(default=None, ge=0)
    selected_candidate_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("decision_at")
    @classmethod
    def normalize_decision_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("arbitration decision time must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        if not value or value != value.strip().upper():
            raise ValueError("arbitration symbol must be uppercase")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> CandidateArbitration:
        if self.status == "SELECTED":
            if (
                self.rejection_reason is not None
                or self.priority is None
                or self.selected_candidate_sha256 != self.candidate_sha256
            ):
                raise ValueError("selected arbitration result is inconsistent")
        elif self.rejection_reason is None or self.priority is not None:
            raise ValueError("rejected arbitration result is inconsistent")
        if (
            self.rejection_reason
            in {
                "LOWER_STRATEGY_PRIORITY",
                "SYMBOL_COOLDOWN",
            }
            and self.selected_candidate_sha256 is None
        ):
            raise ValueError("conflict rejection requires selected-candidate lineage")
        if (
            self.rejection_reason
            in {
                "UPSTREAM_BLOCKED",
                "BELOW_MINIMUM_STRENGTH",
                "CLOCK_CAPACITY",
            }
            and self.selected_candidate_sha256 is not None
        ):
            raise ValueError("non-conflict rejection cannot reference a selected candidate")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


def require_selected_candidate(
    candidate: BaselineCandidate,
    arbitration: CandidateArbitration,
) -> None:
    """Validate the canonical candidate-to-selection lineage once at every boundary."""
    if (
        not candidate.is_candidate
        or arbitration.status != "SELECTED"
        or arbitration.candidate_version != candidate.baseline_version
        or arbitration.candidate_sha256 != candidate.sha256
        or arbitration.strategy != candidate.strategy
        or arbitration.symbol != candidate.symbol
        or arbitration.trade_date != candidate.trade_date
        or arbitration.decision_at != candidate.decision_at
        or arbitration.strength != candidate.strength
        or arbitration.selected_candidate_sha256 != candidate.sha256
    ):
        raise ValueError("candidate and selected arbitration lineage do not match")
