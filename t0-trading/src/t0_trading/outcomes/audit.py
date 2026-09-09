"""Deterministic quality summary for offline execution outcomes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.configuration import OutcomeVersion, TradingVersion
from t0_trading.features import FeatureSnapshot
from t0_trading.numeric import quantiles, rate
from t0_trading.outcomes.model import Action, OutcomeLabel

_ACTIONS: tuple[Action, ...] = ("BUY", "SELL")
_LabelKey = tuple[str, Action, int]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReturnDistribution(_StrictModel):
    count: int = Field(ge=1)
    minimum: Decimal
    p05: Decimal
    p50: Decimal
    p95: Decimal
    maximum: Decimal

    @model_validator(mode="after")
    def validate_order(self) -> ReturnDistribution:
        if not self.minimum <= self.p05 <= self.p50 <= self.p95 <= self.maximum:
            raise ValueError("outcome distribution quantiles must be ordered")
        return self


class HorizonOutcomeAudit(_StrictModel):
    horizon_seconds: int = Field(ge=1)
    label_count: int = Field(ge=1)
    eligible_count: int = Field(ge=0)
    eligible_rate: Decimal = Field(ge=0, le=1)
    entry_filled_count: int = Field(ge=0)
    entry_fill_rate: Decimal = Field(ge=0, le=1)
    horizon_filled_count: int = Field(ge=0)
    horizon_fill_rate: Decimal = Field(ge=0, le=1)
    positive_return_count: int = Field(ge=0)
    zero_return_count: int = Field(ge=0)
    negative_return_count: int = Field(ge=0)
    positive_return_rate: Decimal = Field(ge=0, le=1)
    negative_return_rate: Decimal = Field(ge=0, le=1)
    reason_counts: dict[str, int]
    eligible_gross_return_bps: ReturnDistribution | None

    @model_validator(mode="after")
    def validate_summary(self) -> HorizonOutcomeAudit:
        counts = (
            self.eligible_count,
            self.entry_filled_count,
            self.horizon_filled_count,
        )
        distribution_count = (
            self.eligible_gross_return_bps.count
            if self.eligible_gross_return_bps is not None
            else 0
        )
        if (
            any(count > self.label_count for count in counts)
            or self.positive_return_count + self.zero_return_count + self.negative_return_count
            != self.eligible_count
            or self.eligible_rate != rate(self.eligible_count, self.label_count)
            or self.entry_fill_rate != rate(self.entry_filled_count, self.label_count)
            or self.horizon_fill_rate != rate(self.horizon_filled_count, self.label_count)
            or self.positive_return_rate != rate(self.positive_return_count, self.eligible_count)
            or self.negative_return_rate != rate(self.negative_return_count, self.eligible_count)
            or distribution_count != self.eligible_count
            or any(count < 1 for count in self.reason_counts.values())
        ):
            raise ValueError("horizon outcome audit does not reconcile")
        return self


class ActionOutcomeAudit(_StrictModel):
    snapshot_count: int = Field(ge=1)
    fully_eligible_path_count: int = Field(ge=0)
    fully_eligible_path_rate: Decimal = Field(ge=0, le=1)
    worst_observed_markout_bps: ReturnDistribution | None
    horizons: tuple[HorizonOutcomeAudit, ...]

    @model_validator(mode="after")
    def validate_summary(self) -> ActionOutcomeAudit:
        distribution_count = (
            self.worst_observed_markout_bps.count
            if self.worst_observed_markout_bps is not None
            else 0
        )
        if (
            not self.horizons
            or tuple(item.horizon_seconds for item in self.horizons)
            != tuple(sorted({item.horizon_seconds for item in self.horizons}))
            or any(item.label_count != self.snapshot_count for item in self.horizons)
            or self.fully_eligible_path_count > self.snapshot_count
            or self.fully_eligible_path_rate
            != rate(self.fully_eligible_path_count, self.snapshot_count)
            or distribution_count != self.fully_eligible_path_count
        ):
            raise ValueError("action outcome audit does not reconcile")
        return self


class DirectionalPairAudit(_StrictModel):
    horizon_seconds: int = Field(ge=1)
    pair_count: int = Field(ge=1)
    jointly_eligible_count: int = Field(ge=0)
    jointly_eligible_rate: Decimal = Field(ge=0, le=1)
    paired_directional_sum_bps: ReturnDistribution | None

    @model_validator(mode="after")
    def validate_summary(self) -> DirectionalPairAudit:
        distribution_count = (
            self.paired_directional_sum_bps.count
            if self.paired_directional_sum_bps is not None
            else 0
        )
        if (
            self.jointly_eligible_count > self.pair_count
            or self.jointly_eligible_rate != rate(self.jointly_eligible_count, self.pair_count)
            or distribution_count != self.jointly_eligible_count
        ):
            raise ValueError("directional pair audit does not reconcile")
        return self


class SymbolOutcomeAudit(_StrictModel):
    snapshot_count: int = Field(ge=1)
    actions: dict[Action, ActionOutcomeAudit]
    directional_pairs: tuple[DirectionalPairAudit, ...]

    @model_validator(mode="after")
    def validate_summary(self) -> SymbolOutcomeAudit:
        if (
            set(self.actions) != set(_ACTIONS)
            or any(item.snapshot_count != self.snapshot_count for item in self.actions.values())
            or tuple(item.horizon_seconds for item in self.directional_pairs)
            != tuple(item.horizon_seconds for item in self.actions["BUY"].horizons)
            or any(item.pair_count != self.snapshot_count for item in self.directional_pairs)
        ):
            raise ValueError("symbol outcome audit does not reconcile")
        return self


class OutcomeAuditReport(_StrictModel):
    """Stable JSON report for labels derived from one terminal stream session."""

    schema_version: Literal[1] = 1
    manifest_uri: str = Field(pattern=r"^s3://")
    stream_session_id: str = Field(min_length=1)
    trade_date: date
    configuration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    outcome_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_message_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=1)
    label_count: int = Field(ge=1)
    eligible_count: int = Field(ge=0)
    eligible_rate: Decimal = Field(ge=0, le=1)
    symbols: dict[str, SymbolOutcomeAudit]

    @model_validator(mode="after")
    def validate_totals(self) -> OutcomeAuditReport:
        symbol_labels = sum(
            horizon.label_count
            for symbol in self.symbols.values()
            for action in symbol.actions.values()
            for horizon in action.horizons
        )
        symbol_eligible = sum(
            horizon.eligible_count
            for symbol in self.symbols.values()
            for action in symbol.actions.values()
            for horizon in action.horizons
        )
        if (
            not self.symbols
            or any(symbol != symbol.strip().upper() for symbol in self.symbols)
            or sum(item.snapshot_count for item in self.symbols.values()) != self.snapshot_count
            or symbol_labels != self.label_count
            or symbol_eligible != self.eligible_count
            or self.eligible_rate != rate(self.eligible_count, self.label_count)
        ):
            raise ValueError("outcome audit totals do not reconcile")
        return self


def _distribution(values: Sequence[Decimal]) -> ReturnDistribution | None:
    if not values:
        return None
    minimum, p05, p50, p95, maximum = quantiles(
        values,
        (
            Decimal(0),
            Decimal("0.05"),
            Decimal("0.50"),
            Decimal("0.95"),
            Decimal(1),
        ),
    )
    return ReturnDistribution(
        count=len(values),
        minimum=minimum,
        p05=p05,
        p50=p50,
        p95=p95,
        maximum=maximum,
    )


def _horizon_audit(
    labels: Sequence[OutcomeLabel],
    *,
    horizon_seconds: int,
) -> HorizonOutcomeAudit:
    returns = tuple(
        label.gross_return_bps
        for label in labels
        if label.is_eligible and label.gross_return_bps is not None
    )
    eligible_count = len(returns)
    positive_count = sum(value > 0 for value in returns)
    zero_count = sum(value == 0 for value in returns)
    negative_count = sum(value < 0 for value in returns)
    entry_filled_count = sum(label.entry_vwap is not None for label in labels)
    horizon_filled_count = sum(label.horizon_vwap is not None for label in labels)
    return HorizonOutcomeAudit(
        horizon_seconds=horizon_seconds,
        label_count=len(labels),
        eligible_count=eligible_count,
        eligible_rate=rate(eligible_count, len(labels)),
        entry_filled_count=entry_filled_count,
        entry_fill_rate=rate(entry_filled_count, len(labels)),
        horizon_filled_count=horizon_filled_count,
        horizon_fill_rate=rate(horizon_filled_count, len(labels)),
        positive_return_count=positive_count,
        zero_return_count=zero_count,
        negative_return_count=negative_count,
        positive_return_rate=rate(positive_count, eligible_count),
        negative_return_rate=rate(negative_count, eligible_count),
        reason_counts=dict(
            sorted(Counter(reason for label in labels for reason in label.reasons).items())
        ),
        eligible_gross_return_bps=_distribution(returns),
    )


def _action_audit(
    snapshot_hashes: Sequence[str],
    labels_by_key: dict[_LabelKey, OutcomeLabel],
    horizons_seconds: tuple[int, ...],
    action: Action,
) -> ActionOutcomeAudit:
    horizons = tuple(
        _horizon_audit(
            tuple(
                labels_by_key[(snapshot_hash, action, horizon)] for snapshot_hash in snapshot_hashes
            ),
            horizon_seconds=horizon,
        )
        for horizon in horizons_seconds
    )
    worst_returns: list[Decimal] = []
    for snapshot_hash in snapshot_hashes:
        path = tuple(
            labels_by_key[(snapshot_hash, action, horizon)] for horizon in horizons_seconds
        )
        if all(label.is_eligible for label in path):
            returns = tuple(
                label.gross_return_bps for label in path if label.gross_return_bps is not None
            )
            if len(returns) != len(path):
                raise ValueError("eligible outcome path is missing a return")
            worst_returns.append(min(returns))
    return ActionOutcomeAudit(
        snapshot_count=len(snapshot_hashes),
        fully_eligible_path_count=len(worst_returns),
        fully_eligible_path_rate=rate(len(worst_returns), len(snapshot_hashes)),
        worst_observed_markout_bps=_distribution(worst_returns),
        horizons=horizons,
    )


def _directional_pairs(
    snapshot_hashes: Sequence[str],
    labels_by_key: dict[_LabelKey, OutcomeLabel],
    horizons_seconds: tuple[int, ...],
) -> tuple[DirectionalPairAudit, ...]:
    reports: list[DirectionalPairAudit] = []
    for horizon in horizons_seconds:
        paired_returns: list[Decimal] = []
        for snapshot_hash in snapshot_hashes:
            buy = labels_by_key[(snapshot_hash, "BUY", horizon)]
            sell = labels_by_key[(snapshot_hash, "SELL", horizon)]
            if buy.is_eligible and sell.is_eligible:
                if buy.gross_return_bps is None or sell.gross_return_bps is None:
                    raise ValueError("eligible directional pair is missing a return")
                paired_returns.append(buy.gross_return_bps + sell.gross_return_bps)
        reports.append(
            DirectionalPairAudit(
                horizon_seconds=horizon,
                pair_count=len(snapshot_hashes),
                jointly_eligible_count=len(paired_returns),
                jointly_eligible_rate=rate(len(paired_returns), len(snapshot_hashes)),
                paired_directional_sum_bps=_distribution(paired_returns),
            )
        )
    return tuple(reports)


def build_outcome_audit(
    snapshots: Sequence[FeatureSnapshot],
    labels: Sequence[OutcomeLabel],
    configuration: TradingVersion,
    policy: OutcomeVersion,
    *,
    trade_date: date,
    manifest_uri: str,
    stream_session_id: str,
    input_message_count: int,
) -> OutcomeAuditReport:
    """Reconcile and summarize a complete label matrix for validated feature snapshots."""
    if not snapshots:
        raise ValueError("outcome audit requires feature snapshots")
    snapshot_by_hash = {snapshot.sha256: snapshot for snapshot in snapshots}
    if len(snapshot_by_hash) != len(snapshots):
        raise ValueError("outcome audit feature snapshot identities are not unique")

    expected_keys = {
        (snapshot_sha256, action, horizon)
        for snapshot_sha256 in snapshot_by_hash
        for action in _ACTIONS
        for horizon in policy.horizons_seconds
    }
    observed_keys = {
        (label.feature_snapshot_sha256, label.action, label.horizon_seconds) for label in labels
    }
    if len(observed_keys) != len(labels) or observed_keys != expected_keys:
        raise ValueError("outcome labels do not cover every snapshot/action/horizon exactly once")

    policy_sha256 = policy.sha256
    for label in labels:
        snapshot = snapshot_by_hash[label.feature_snapshot_sha256]
        if (
            label.outcome_version != policy.version
            or label.outcome_configuration_sha256 != policy_sha256
            or label.feature_version != configuration.features.version
            or label.feature_configuration_sha256 != configuration.sha256
            or label.stream_session_id != stream_session_id
            or label.trade_date != trade_date
            or label.symbol != snapshot.symbol
            or label.decision_at != snapshot.decision_at
            or label.order_quantity != policy.order_quantity
            or (
                label.entry_receive_sequence is not None
                and label.entry_receive_sequence > input_message_count
            )
            or (
                label.horizon_receive_sequence is not None
                and label.horizon_receive_sequence > input_message_count
            )
        ):
            raise ValueError("outcome audit lineage is inconsistent")
    if (
        not configuration.contains(trade_date)
        or not policy.contains(trade_date)
        or any(
            snapshot.trade_date != trade_date
            or snapshot.stream_session_id != stream_session_id
            or snapshot.configuration_version != configuration.version
            or snapshot.configuration_sha256 != configuration.sha256
            or snapshot.feature_version != configuration.features.version
            or snapshot.symbol not in configuration.market.symbols
            or (
                snapshot.last_receive_sequence is not None
                and snapshot.last_receive_sequence > input_message_count
            )
            for snapshot in snapshots
        )
    ):
        raise ValueError("outcome audit feature lineage is inconsistent")

    labels_by_key: dict[_LabelKey, OutcomeLabel] = {
        (label.feature_snapshot_sha256, label.action, label.horizon_seconds): label
        for label in labels
    }
    symbols: dict[str, SymbolOutcomeAudit] = {}
    for symbol in configuration.market.symbols:
        selected = tuple(
            (snapshot_hash, snapshot)
            for snapshot_hash, snapshot in snapshot_by_hash.items()
            if snapshot.symbol == symbol
        )
        if not selected:
            raise ValueError("outcome audit does not cover every configured symbol")
        snapshot_hashes = tuple(snapshot_hash for snapshot_hash, _ in selected)
        symbols[symbol] = SymbolOutcomeAudit(
            snapshot_count=len(selected),
            actions={
                action: _action_audit(
                    snapshot_hashes,
                    labels_by_key,
                    policy.horizons_seconds,
                    action,
                )
                for action in _ACTIONS
            },
            directional_pairs=_directional_pairs(
                snapshot_hashes,
                labels_by_key,
                policy.horizons_seconds,
            ),
        )

    eligible_count = sum(label.is_eligible for label in labels)
    return OutcomeAuditReport(
        manifest_uri=manifest_uri,
        stream_session_id=stream_session_id,
        trade_date=trade_date,
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        feature_version=configuration.features.version,
        outcome_version=policy.version,
        outcome_configuration_sha256=policy_sha256,
        input_message_count=input_message_count,
        snapshot_count=len(snapshots),
        label_count=len(labels),
        eligible_count=eligible_count,
        eligible_rate=rate(eligible_count, len(labels)),
        symbols=symbols,
    )
