"""Receipt-time breadth from captured membership and closed SSI minute revisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.capture.membership import BreadthMembershipSnapshot
from t0_trading.configuration import BreadthVersion, TradingVersion
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.events import StreamEnvelope
from t0_trading.market.intervals import ProviderBar, provider_bar
from t0_trading.market.session import MarketSession, session_at
from t0_trading.numeric import RATIO_QUANTUM, basis_points, ratio

_MINUTE = timedelta(minutes=1)
_CONTINUOUS = {MarketSession.CONTINUOUS_AM, MarketSession.CONTINUOUS_PM}


class BreadthContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: str
    policy_version: str
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_kind: Literal["ssi_stream_interval"] = "ssi_stream_interval"
    membership_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    observations_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lookback_seconds: int = Field(ge=60)
    expected_count: int = Field(ge=0)
    observed_count: int = Field(ge=0)
    participation: Decimal | None = Field(default=None, ge=0, le=1)
    advance_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    mean_return_bps: Decimal | None = None
    dispersion_bps: Decimal | None = Field(default=None, ge=0)
    upward_confirmation: bool | None = None
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_metrics(self) -> BreadthContext:
        if self.observed_count > self.expected_count:
            raise ValueError("breadth observations exceed membership")
        if (self.participation is None) != (self.expected_count == 0):
            raise ValueError("breadth participation requires a membership denominator")
        values = (self.advance_ratio, self.mean_return_bps, self.dispersion_bps)
        if any(value is None for value in values) != (self.observed_count == 0) or any(
            value is not None for value in values
        ) != (self.observed_count > 0):
            raise ValueError("breadth metrics require observed constituents")
        if self.participation is not None and self.participation != ratio(
            self.observed_count, self.expected_count
        ):
            raise ValueError("breadth participation does not match coverage")
        if (self.upward_confirmation is None) != bool(self.reasons):
            raise ValueError("breadth confirmation requires healthy evidence")
        return self


@dataclass(frozen=True, slots=True)
class _Revision:
    bar: ProviderBar
    received_at: datetime
    position: tuple[str, int, str]


class BreadthEngine:
    """One bounded implementation for live and replay. Never forward-fill missing minutes."""

    def __init__(
        self,
        configuration: TradingVersion,
        policy: BreadthVersion,
        membership: BreadthMembershipSnapshot | None,
    ) -> None:
        self._configuration = configuration
        self._policy = policy
        self._policy_sha256 = policy.sha256
        self._membership = membership
        self._timezone = ZoneInfo(configuration.market.timezone)
        self._symbols = set(membership.symbols) if membership is not None else set()
        self._bars: dict[tuple[str, datetime], _Revision] = {}
        self._last_received_at: datetime | None = None
        self._last_decision_at: datetime | None = None

    def apply(self, envelope: StreamEnvelope) -> None:
        if self._last_received_at is not None and envelope.received_at < self._last_received_at:
            raise ValueError("breadth envelopes must be receipt ordered")
        self._last_received_at = envelope.received_at
        if envelope.symbol not in self._symbols:
            return
        bar = provider_bar(envelope, self._timezone)
        if bar is None:
            return
        key = (bar.symbol, bar.start)
        previous = self._bars.get(key)
        # An older provider update delivered late must not rewind the minute.
        if previous is None or bar.observed_at >= previous.bar.observed_at:
            self._bars[key] = _Revision(
                bar,
                envelope.received_at,
                (envelope.stream_session_id, envelope.receive_sequence, envelope.message_sha256),
            )
        cutoff = envelope.received_at - timedelta(
            seconds=self._policy.lookback_seconds + self._policy.stale_after_seconds + 60
        )
        self._bars = {key: item for key, item in self._bars.items() if key[1] >= cutoff}

    def build(self, at: datetime) -> tuple[BreadthContext, ...]:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("breadth decision time must be timezone-aware")
        if self._last_received_at is not None and self._last_received_at > at:
            raise ValueError("breadth cannot build before applied receipts")
        if self._last_decision_at is not None and at <= self._last_decision_at:
            raise ValueError("breadth clocks must be strictly increasing")
        trade_date = at.astimezone(self._timezone).date()
        if not self._policy.contains(trade_date):
            raise ValueError("breadth policy must cover the decision date")
        self._last_decision_at = at
        current_start = at.replace(second=0, microsecond=0) - _MINUTE
        base_start = current_start - timedelta(seconds=self._policy.lookback_seconds)
        session = session_at(
            at,
            trade_date=trade_date,
            timezone=self._timezone,
            schedule=self._configuration.market.sessions,
        )
        same_session = (
            session in _CONTINUOUS
            and session_at(
                base_start,
                trade_date=trade_date,
                timezone=self._timezone,
                schedule=self._configuration.market.sessions,
            )
            == session
        )
        snapshot = self._membership
        membership_known = (
            snapshot is not None
            and snapshot.captured_at <= at
            and snapshot.captured_at.astimezone(self._timezone).date() == trade_date
        )
        memberships = (
            {item.index: item for item in snapshot.memberships}
            if membership_known and snapshot
            else {}
        )
        result = []
        for index in sorted(self._policy.indices):
            membership = memberships.get(index)
            symbols = membership.symbols if membership is not None else ()
            returns: list[Decimal] = []
            lineage: list[tuple[object, ...]] = []
            for symbol in symbols if same_session else ():
                current = self._bars.get((symbol, current_start))
                baseline = self._bars.get((symbol, base_start))
                if current is None or baseline is None:
                    continue
                if any(
                    (boundary - item.bar.observed_at).total_seconds()
                    > self._policy.stale_after_seconds
                    for boundary, item in (
                        (at, current),
                        (at - timedelta(seconds=self._policy.lookback_seconds), baseline),
                    )
                ):
                    continue
                returns.append(
                    basis_points(
                        current.bar.close_price - baseline.bar.close_price, baseline.bar.close_price
                    )
                )
                lineage.append(
                    (
                        symbol,
                        baseline.position,
                        baseline.received_at.isoformat(),
                        current.position,
                        current.received_at.isoformat(),
                    )
                )
            count = len(returns)
            participation = ratio(count, len(symbols)) if symbols else None
            advance = ratio(sum(value > 0 for value in returns), count) if count else None
            mean = sum(returns, Decimal(0)) / count if count else None
            dispersion = (
                (sum(((value - mean) ** 2 for value in returns), Decimal(0)) / count)
                .sqrt()
                .quantize(RATIO_QUANTUM)
                if mean is not None
                else None
            )
            reasons: list[str] = []
            if not symbols:
                reasons.append("membership_unavailable")
            if not same_session:
                reasons.append("outside_continuous_lookback")
            if not count:
                reasons.append("missing_closed_minute_pairs")
            if participation is not None and participation < self._policy.minimum_participation:
                reasons.append("insufficient_participation")
            result.append(
                BreadthContext(
                    index=index,
                    policy_version=self._policy.version,
                    policy_sha256=self._policy_sha256,
                    membership_sha256=snapshot.snapshot_sha256
                    if membership_known and snapshot
                    else None,
                    observations_sha256=sha256(canonical_json(lineage)),
                    lookback_seconds=self._policy.lookback_seconds,
                    expected_count=len(symbols),
                    observed_count=count,
                    participation=participation,
                    advance_ratio=advance,
                    mean_return_bps=mean.quantize(RATIO_QUANTUM) if mean is not None else None,
                    dispersion_bps=dispersion,
                    upward_confirmation=(
                        advance >= self._policy.confirmation_advance_ratio and mean > 0
                    )
                    if not reasons and advance is not None and mean is not None
                    else None,
                    reasons=tuple(reasons),
                )
            )
        return tuple(result)
