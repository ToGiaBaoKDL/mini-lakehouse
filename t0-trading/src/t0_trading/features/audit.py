"""Deterministic quality summary for one full-session feature replay."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.configuration import TradingVersion
from t0_trading.features.engine import decision_times
from t0_trading.features.model import FeatureSnapshot
from t0_trading.numeric import quantiles, rate

_SNAPSHOT_FIELDS = (
    "trade_age_seconds",
    "quote_age_seconds",
    "mid_price",
    "microprice",
    "microprice_deviation_bps",
    "spread",
    "spread_bps",
    "bid_depth",
    "ask_depth",
    "level_one_imbalance",
    "depth_imbalance",
)
_WINDOW_FIELDS = (
    "trade_count",
    "quote_change_count",
    "trade_volume",
    "signed_trade_volume",
    "trade_volume_per_second",
    "trade_volume_imbalance",
    "level_one_order_flow_imbalance",
    "price_return_bps",
    "realized_volatility_bps",
    "vwap",
    "last_price_to_vwap_bps",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Distribution(_StrictModel):
    count: int = Field(ge=1)
    minimum: Decimal
    p50: Decimal
    p95: Decimal
    maximum: Decimal

    @model_validator(mode="after")
    def validate_order(self) -> Distribution:
        if not self.minimum <= self.p50 <= self.p95 <= self.maximum:
            raise ValueError("feature distribution quantiles must be ordered")
        return self


class Coverage(_StrictModel):
    snapshot_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    eligible_rate: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_counts(self) -> Coverage:
        if self.eligible_count > self.snapshot_count or self.eligible_rate != rate(
            self.eligible_count, self.snapshot_count
        ):
            raise ValueError("feature coverage counts are inconsistent")
        return self


class SymbolFeatureAudit(Coverage):
    sessions: dict[str, Coverage]
    reason_counts: dict[str, int]
    null_rates: dict[str, Decimal]
    eligible_distributions: dict[str, Distribution]

    @model_validator(mode="after")
    def validate_summary(self) -> SymbolFeatureAudit:
        if (
            sum(item.snapshot_count for item in self.sessions.values()) != self.snapshot_count
            or sum(item.eligible_count for item in self.sessions.values()) != self.eligible_count
            or any(count < 1 for count in self.reason_counts.values())
            or any(rate < 0 or rate > 1 for rate in self.null_rates.values())
        ):
            raise ValueError("symbol feature audit does not reconcile")
        return self


class FeatureAuditReport(_StrictModel):
    """Stable JSON report for one terminal manifest and feature version."""

    schema_version: Literal[1] = 1
    manifest_uri: str = Field(pattern=r"^s3://")
    stream_session_id: str = Field(min_length=1)
    trade_date: date
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    configuration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_message_count: int = Field(ge=0)
    last_decision_receive_sequence: int | None = Field(ge=1)
    first_decision_at: datetime
    last_decision_at: datetime
    snapshot_count: int = Field(ge=1)
    symbols: dict[str, SymbolFeatureAudit]

    @model_validator(mode="after")
    def validate_totals(self) -> FeatureAuditReport:
        if (
            not self.symbols
            or any(symbol != symbol.strip().upper() for symbol in self.symbols)
            or sum(item.snapshot_count for item in self.symbols.values()) != self.snapshot_count
            or self.first_decision_at > self.last_decision_at
            or (
                self.last_decision_receive_sequence is not None
                and self.last_decision_receive_sequence > self.input_message_count
            )
        ):
            raise ValueError("feature audit totals are inconsistent")
        return self


def _number(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError("audited feature value must be numeric or null")
    return Decimal(value)


def _distribution(values: Sequence[Decimal]) -> Distribution:
    minimum, p50, p95, maximum = quantiles(
        values,
        (Decimal(0), Decimal("0.50"), Decimal("0.95"), Decimal(1)),
    )

    return Distribution(
        count=len(values),
        minimum=minimum,
        p50=p50,
        p95=p95,
        maximum=maximum,
    )


def _null_rate(values: Sequence[object]) -> Decimal:
    present_count = sum(_number(value) is not None for value in values)
    return rate(len(values) - present_count, len(values))


def _summary(values: Sequence[object]) -> Distribution | None:
    present = tuple(value for raw in values if (value := _number(raw)) is not None)
    return _distribution(present) if present else None


def build_feature_audit(
    snapshots: Sequence[FeatureSnapshot],
    configuration: TradingVersion,
    *,
    trade_date: date,
    manifest_uri: str,
    stream_session_id: str,
    input_message_count: int,
) -> FeatureAuditReport:
    """Validate and summarize one complete point-in-time feature replay."""
    expected_times = tuple(decision_times(configuration, trade_date))
    expected_keys = {
        (symbol, decision_at)
        for decision_at in expected_times
        for symbol in configuration.market.symbols
    }
    observed_keys = {(snapshot.symbol, snapshot.decision_at) for snapshot in snapshots}
    if len(observed_keys) != len(snapshots) or observed_keys != expected_keys:
        raise ValueError("feature replay does not cover every configured decision key exactly once")
    if any(
        snapshot.trade_date != trade_date
        or snapshot.feature_version != configuration.features.version
        or snapshot.configuration_version != configuration.version
        or snapshot.configuration_sha256 != configuration.sha256
        or snapshot.stream_session_id != stream_session_id
        or tuple(window.window_seconds for window in snapshot.windows)
        != configuration.features.windows_seconds
        for snapshot in snapshots
    ):
        raise ValueError("feature replay lineage is inconsistent")
    if any(
        any(getattr(snapshot, field) is None for field in _SNAPSHOT_FIELDS)
        or any(
            getattr(window, field) is None
            for window in snapshot.windows
            for field in _WINDOW_FIELDS
        )
        for snapshot in snapshots
        if snapshot.is_eligible
    ):
        raise ValueError("eligible feature snapshot contains a null feature")

    grouped: dict[str, list[FeatureSnapshot]] = {
        symbol: [] for symbol in configuration.market.symbols
    }
    for snapshot in snapshots:
        grouped[snapshot.symbol].append(snapshot)

    symbol_reports: dict[str, SymbolFeatureAudit] = {}
    for symbol in configuration.market.symbols:
        selected = tuple(grouped[symbol])
        eligible = tuple(snapshot for snapshot in selected if snapshot.is_eligible)
        session_coverage: dict[str, Coverage] = {}
        for session in configuration.features.decision_sessions:
            in_session = tuple(
                snapshot for snapshot in selected if snapshot.market_session.value == session
            )
            eligible_count = sum(snapshot.is_eligible for snapshot in in_session)
            session_coverage[session] = Coverage(
                snapshot_count=len(in_session),
                eligible_count=eligible_count,
                eligible_rate=rate(eligible_count, len(in_session)),
            )

        null_rates: dict[str, Decimal] = {}
        distributions: dict[str, Distribution] = {}
        for field in _SNAPSHOT_FIELDS:
            null_rates[field] = _null_rate(tuple(getattr(snapshot, field) for snapshot in selected))
            summary = _summary(tuple(getattr(snapshot, field) for snapshot in eligible))
            if summary is not None:
                distributions[field] = summary
        for seconds in configuration.features.windows_seconds:
            windows = tuple(
                next(window for window in snapshot.windows if window.window_seconds == seconds)
                for snapshot in selected
            )
            eligible_windows = tuple(
                next(window for window in snapshot.windows if window.window_seconds == seconds)
                for snapshot in eligible
            )
            for field in _WINDOW_FIELDS:
                key = f"{seconds}s.{field}"
                null_rates[key] = _null_rate(tuple(getattr(window, field) for window in windows))
                summary = _summary(tuple(getattr(window, field) for window in eligible_windows))
                if summary is not None:
                    distributions[key] = summary

        eligible_count = len(eligible)
        symbol_reports[symbol] = SymbolFeatureAudit(
            snapshot_count=len(selected),
            eligible_count=eligible_count,
            eligible_rate=rate(eligible_count, len(selected)),
            sessions=session_coverage,
            reason_counts=dict(
                sorted(
                    Counter(reason for snapshot in selected for reason in snapshot.reasons).items()
                )
            ),
            null_rates=null_rates,
            eligible_distributions=distributions,
        )

    receive_sequences = tuple(
        snapshot.last_receive_sequence
        for snapshot in snapshots
        if snapshot.last_receive_sequence is not None
    )
    return FeatureAuditReport(
        manifest_uri=manifest_uri,
        stream_session_id=stream_session_id,
        trade_date=trade_date,
        feature_version=configuration.features.version,
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        input_message_count=input_message_count,
        last_decision_receive_sequence=max(receive_sequences, default=None),
        first_decision_at=expected_times[0],
        last_decision_at=expected_times[-1],
        snapshot_count=len(snapshots),
        symbols=symbol_reports,
    )
