"""Deterministic evidence contract for shadow-to-paper promotion."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.identity import canonical_json, sha256
from t0_trading.strategy.baselines import BaselineName

PromotionStatus = Literal["PENDING", "PASS", "FAIL"]
PromotionReason = Literal[
    "INSUFFICIENT_SESSIONS",
    "CAPTURE_GAPS",
    "INSUFFICIENT_SELECTIONS",
    "MISSING_FOLD_OUTCOMES",
    "OUTCOME_COVERAGE",
    "POSITIVE_NET_RATE",
    "AVERAGE_NET_RETURN",
    "WORST_FOLD_NET_RETURN",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PromotionTargetResult(_StrictModel):
    strategy: BaselineName
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]*$")
    horizon_seconds: int = Field(ge=1)
    status: PromotionStatus
    reasons: tuple[PromotionReason, ...]
    selected_count: int = Field(ge=0)
    eligible_outcome_count: int = Field(ge=0)
    positive_net_count: int = Field(ge=0)
    outcome_coverage_rate: Decimal = Field(ge=0, le=1)
    positive_net_rate: Decimal = Field(ge=0, le=1)
    average_net_return_bps: Decimal | None
    worst_fold_net_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_result(self) -> PromotionTargetResult:
        if not self.positive_net_count <= self.eligible_outcome_count <= self.selected_count:
            raise ValueError("promotion target counts are inconsistent")
        if tuple(dict.fromkeys(self.reasons)) != self.reasons:
            raise ValueError("promotion target reasons must be unique and ordered")
        if (self.status == "PASS") != (not self.reasons):
            raise ValueError("promotion target status and reasons are inconsistent")
        return self


class ArbitratedSessionEvaluation(_StrictModel):
    """One daily result over candidates actually selected by arbitration."""

    strategy: BaselineName
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]*$")
    horizon_seconds: int = Field(ge=1)
    selected_count: int = Field(ge=0)
    eligible_outcome_count: int = Field(ge=0)
    positive_net_count: int = Field(ge=0)
    average_conditional_net_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_counts(self) -> ArbitratedSessionEvaluation:
        if not self.positive_net_count <= self.eligible_outcome_count <= self.selected_count:
            raise ValueError("arbitrated session counts are inconsistent")
        if (self.eligible_outcome_count == 0) != (
            self.average_conditional_net_return_bps is None
        ):
            raise ValueError("arbitrated session average requires eligible outcomes")
        return self


class ArbitratedSessionReport(_StrictModel):
    """Prospective daily performance for the exact selected shadow population."""

    schema_version: Literal[1] = 1
    trade_date: date
    baseline_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shadow_audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_gap_count: int = Field(ge=0)
    evaluations: tuple[ArbitratedSessionEvaluation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evaluations(self) -> ArbitratedSessionReport:
        keys = tuple(
            (item.strategy, item.symbol, item.horizon_seconds) for item in self.evaluations
        )
        if len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
            raise ValueError("arbitrated session evaluations must be unique and ordered")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class ArbitratedHoldoutEvaluation(_StrictModel):
    strategy: BaselineName
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]*$")
    horizon_seconds: int = Field(ge=1)
    selected_count: int = Field(ge=0)
    eligible_outcome_count: int = Field(ge=0)
    positive_net_count: int = Field(ge=0)
    average_conditional_net_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_counts(self) -> ArbitratedHoldoutEvaluation:
        if not self.positive_net_count <= self.eligible_outcome_count <= self.selected_count:
            raise ValueError("arbitrated holdout counts are inconsistent")
        if (self.eligible_outcome_count == 0) != (
            self.average_conditional_net_return_bps is None
        ):
            raise ValueError("arbitrated holdout average requires eligible outcomes")
        return self


class ArbitratedWalkForwardFold(_StrictModel):
    fold: int = Field(ge=1)
    development_dates: tuple[date, ...] = Field(min_length=1)
    purged_dates: tuple[date, ...] = Field(min_length=1)
    holdout_dates: tuple[date, ...] = Field(min_length=1)
    evaluations: tuple[ArbitratedHoldoutEvaluation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fold(self) -> ArbitratedWalkForwardFold:
        groups = (self.development_dates, self.purged_dates, self.holdout_dates)
        keys = tuple(
            (item.strategy, item.symbol, item.horizon_seconds) for item in self.evaluations
        )
        if any(tuple(sorted(set(values))) != values for values in groups) or not (
            self.development_dates[-1]
            < self.purged_dates[0]
            <= self.purged_dates[-1]
            < self.holdout_dates[0]
        ):
            raise ValueError("arbitrated walk-forward dates must be ordered and disjoint")
        if len(set(keys)) != len(keys):
            raise ValueError("arbitrated walk-forward targets must be unique")
        return self


class ArbitratedWalkForwardReport(_StrictModel):
    schema_version: Literal[1] = 1
    evaluation_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    evaluation_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arbitration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_sha256s: tuple[str, ...] = Field(min_length=1)
    folds: tuple[ArbitratedWalkForwardFold, ...] = Field(min_length=1)
    pending_dates: tuple[date, ...]

    @model_validator(mode="after")
    def validate_report(self) -> ArbitratedWalkForwardReport:
        if tuple(item.fold for item in self.folds) != tuple(range(1, len(self.folds) + 1)):
            raise ValueError("arbitrated walk-forward folds must be contiguous")
        keys = tuple(
            (item.strategy, item.symbol, item.horizon_seconds)
            for item in self.folds[0].evaluations
        )
        if any(
            tuple(
                (item.strategy, item.symbol, item.horizon_seconds)
                for item in fold.evaluations
            )
            != keys
            for fold in self.folds
        ):
            raise ValueError("arbitrated walk-forward fold matrices must match")
        if self.pending_dates and (
            tuple(sorted(set(self.pending_dates))) != self.pending_dates
            or self.pending_dates[0] <= self.folds[-1].holdout_dates[-1]
        ):
            raise ValueError("arbitrated walk-forward pending dates are inconsistent")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class PromotionGateReport(_StrictModel):
    """PASS authorizes paper evaluation only; capital authorization is impossible here."""

    schema_version: Literal[1] = 1
    scope: Literal["SHADOW_TO_PAPER"] = "SHADOW_TO_PAPER"
    gate_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    gate_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    evaluation_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arbitration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_session_count: int = Field(ge=1)
    observed_session_count: int = Field(ge=0)
    holdout_dates: tuple[date, ...]
    capture_gap_count: int = Field(ge=0)
    walk_forward_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shadow_audit_sha256s: tuple[str, ...]
    status: PromotionStatus
    reasons: tuple[PromotionReason, ...]
    targets: tuple[PromotionTargetResult, ...] = Field(min_length=1)
    capital_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_report(self) -> PromotionGateReport:
        dates = self.holdout_dates
        if tuple(sorted(set(dates))) != dates:
            raise ValueError("promotion holdout dates must be unique and ordered")
        target_keys = tuple(
            (item.strategy, item.symbol, item.horizon_seconds) for item in self.targets
        )
        if len(set(target_keys)) != len(target_keys):
            raise ValueError("promotion target results must be unique")
        if tuple(dict.fromkeys(self.reasons)) != self.reasons:
            raise ValueError("promotion report reasons must be unique and ordered")
        expected_status: PromotionStatus = (
            "FAIL"
            if any(item.status == "FAIL" for item in self.targets)
            or "CAPTURE_GAPS" in self.reasons
            else "PENDING"
            if self.reasons or any(item.status == "PENDING" for item in self.targets)
            else "PASS"
        )
        if self.status != expected_status:
            raise ValueError("promotion report status is inconsistent")
        if self.walk_forward_sha256 is None and self.holdout_dates:
            raise ValueError("promotion holdouts require a walk-forward report")
        if self.status == "PASS" and (
            not dates
            or self.walk_forward_sha256 is None
            or len(self.shadow_audit_sha256s) != len(dates)
        ):
            raise ValueError("passing promotion requires complete holdout and shadow evidence")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())
