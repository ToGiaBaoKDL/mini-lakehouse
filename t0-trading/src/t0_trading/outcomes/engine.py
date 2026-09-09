"""Leakage-safe offline Top-3 execution markouts for deterministic features."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from t0_trading.configuration import OutcomeVersion, TradingVersion
from t0_trading.features import FeatureSnapshot
from t0_trading.market import StreamEnvelope
from t0_trading.market.events import MarketEvent, QuoteSnapshot
from t0_trading.market.replay import replay
from t0_trading.market.session import session_at, session_window
from t0_trading.numeric import PRICE_QUANTUM, basis_points
from t0_trading.outcomes.model import Action, OutcomeLabel

_ACTIONS: tuple[Action, ...] = ("BUY", "SELL")


@dataclass(slots=True)
class _QuoteTape:
    positions: list[tuple[datetime, int]] = field(default_factory=list)
    quotes: list[QuoteSnapshot] = field(default_factory=list)

    def append(self, quote: QuoteSnapshot) -> None:
        position = (quote.received_at, quote.position.receive_sequence)
        if self.positions and position <= self.positions[-1]:
            raise ValueError("quote tape must be strictly ordered by receipt and sequence")
        self.positions.append(position)
        self.quotes.append(quote)

    def latest(self, evaluated_at: datetime) -> QuoteSnapshot | None:
        index = bisect_right(self.positions, (evaluated_at, 2**63 - 1)) - 1
        return self.quotes[index] if index >= 0 else None


def _book_vwap(quote: QuoteSnapshot, action: Action, quantity: int) -> Decimal | None:
    levels = quote.asks if action == "BUY" else quote.bids
    remaining = quantity
    notional = Decimal(0)
    for level in levels:
        filled = min(remaining, level.quantity)
        notional += level.price * filled
        remaining -= filled
        if remaining == 0:
            return (notional / quantity).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    return None


def _execution_price(
    tape: _QuoteTape,
    *,
    evaluated_at: datetime,
    action: Action,
    quantity: int,
    stale_after: timedelta,
    phase: str,
) -> tuple[QuoteSnapshot | None, Decimal | None, str | None]:
    quote = tape.latest(evaluated_at)
    if quote is None:
        return None, None, f"MISSING_{phase}_QUOTE"
    if not quote.is_complete:
        return quote, None, f"INCOMPLETE_{phase}_BOOK"
    if (
        evaluated_at - quote.received_at > stale_after
        or evaluated_at - quote.event_time > stale_after
    ):
        return quote, None, f"STALE_{phase}_QUOTE"
    price = _book_vwap(quote, action, quantity)
    if price is None:
        return quote, None, f"INSUFFICIENT_{phase}_DEPTH"
    return quote, price, None


def _quote_tapes(
    envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    *,
    stream_session_id: str,
    trade_date: date,
) -> dict[str, _QuoteTape]:
    tapes = {symbol: _QuoteTape() for symbol in configuration.market.symbols}

    def one_session() -> Iterable[StreamEnvelope]:
        for envelope in envelopes:
            if envelope.stream_session_id != stream_session_id:
                raise ValueError("outcome replay input lineage is inconsistent")
            yield envelope

    def observe(event: MarketEvent) -> None:
        if isinstance(event, QuoteSnapshot):
            tapes[event.symbol].append(event)

    result = replay(
        one_session(),
        configuration,
        trade_date=trade_date,
        observe=observe,
    )
    if result.issues:
        raise ValueError("outcome replay input integrity failed: " + ", ".join(result.issues))
    return tapes


def label_outcomes(
    snapshots: Sequence[FeatureSnapshot],
    envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    policy: OutcomeVersion,
) -> tuple[OutcomeLabel, ...]:
    """Label every snapshot/action/horizon without consulting future state early."""
    if not snapshots:
        return ()
    stream_session_ids = {snapshot.stream_session_id for snapshot in snapshots}
    if None in stream_session_ids or len(stream_session_ids) != 1:
        raise ValueError("outcome snapshots must reference one stream session")
    stream_session_id = next(value for value in stream_session_ids if value is not None)
    trade_date = snapshots[0].trade_date
    timezone = ZoneInfo(configuration.market.timezone)
    snapshot_keys = {(snapshot.symbol, snapshot.decision_at) for snapshot in snapshots}
    if any(
        snapshot.feature_version != configuration.features.version
        or snapshot.configuration_version != configuration.version
        or snapshot.configuration_sha256 != configuration.sha256
        or snapshot.symbol not in configuration.market.symbols
        or snapshot.trade_date != trade_date
        or snapshot.decision_at.astimezone(timezone).date() != trade_date
        or session_at(
            snapshot.decision_at,
            trade_date=trade_date,
            timezone=timezone,
            schedule=configuration.market.sessions,
        )
        != snapshot.market_session
        or snapshot.market_session.value not in configuration.features.decision_sessions
        for snapshot in snapshots
    ) or len(snapshot_keys) != len(snapshots):
        raise ValueError("outcome snapshot configuration lineage is inconsistent")
    if not policy.contains(trade_date):
        raise ValueError("outcome policy is not effective for the snapshot trade date")

    tapes = _quote_tapes(
        envelopes,
        configuration,
        stream_session_id=stream_session_id,
        trade_date=trade_date,
    )
    latency = timedelta(milliseconds=policy.execution_latency_milliseconds)
    stale_after = timedelta(seconds=configuration.data_quality.quote_stale_after_seconds)
    policy_sha256 = policy.sha256
    labels: list[OutcomeLabel] = []
    for snapshot in snapshots:
        _, session_stop = session_window(
            snapshot.trade_date,
            snapshot.market_session,
            timezone=timezone,
            schedule=configuration.market.sessions,
        )
        entry_at = snapshot.decision_at + latency
        entry_executions: dict[Action, tuple[QuoteSnapshot | None, Decimal | None, str | None]] = {}
        snapshot_sha256 = snapshot.sha256
        for horizon in policy.horizons_seconds:
            horizon_at = snapshot.decision_at + timedelta(seconds=horizon)
            outside_session = entry_at >= session_stop or horizon_at >= session_stop
            for action in _ACTIONS:
                reasons = ["FEATURE_INELIGIBLE"] if not snapshot.is_eligible else []
                entry_quote: QuoteSnapshot | None = None
                horizon_quote: QuoteSnapshot | None = None
                entry_vwap: Decimal | None = None
                horizon_vwap: Decimal | None = None
                gross_return_bps: Decimal | None = None
                if outside_session:
                    reasons.append("HORIZON_OUTSIDE_SESSION")
                else:
                    tape = tapes[snapshot.symbol]
                    if action not in entry_executions:
                        entry_executions[action] = _execution_price(
                            tape,
                            evaluated_at=entry_at,
                            action=action,
                            quantity=policy.order_quantity,
                            stale_after=stale_after,
                            phase="ENTRY",
                        )
                    entry_quote, entry_vwap, entry_reason = entry_executions[action]
                    horizon_action: Action = "SELL" if action == "BUY" else "BUY"
                    horizon_quote, horizon_vwap, horizon_reason = _execution_price(
                        tape,
                        evaluated_at=horizon_at,
                        action=horizon_action,
                        quantity=policy.order_quantity,
                        stale_after=stale_after,
                        phase="HORIZON",
                    )
                    if entry_reason is not None:
                        reasons.append(entry_reason)
                    if horizon_reason is not None:
                        reasons.append(horizon_reason)
                    if entry_vwap is not None and horizon_vwap is not None:
                        movement = (
                            horizon_vwap - entry_vwap
                            if action == "BUY"
                            else entry_vwap - horizon_vwap
                        )
                        gross_return_bps = basis_points(movement, entry_vwap)
                labels.append(
                    OutcomeLabel(
                        outcome_version=policy.version,
                        outcome_configuration_sha256=policy_sha256,
                        feature_version=snapshot.feature_version,
                        feature_configuration_sha256=snapshot.configuration_sha256,
                        feature_snapshot_sha256=snapshot_sha256,
                        stream_session_id=stream_session_id,
                        symbol=snapshot.symbol,
                        trade_date=snapshot.trade_date,
                        decision_at=snapshot.decision_at,
                        action=action,
                        horizon_seconds=horizon,
                        order_quantity=policy.order_quantity,
                        entry_at=entry_at,
                        horizon_at=horizon_at,
                        entry_quote_received_at=(
                            entry_quote.received_at if entry_quote is not None else None
                        ),
                        entry_receive_sequence=(
                            entry_quote.position.receive_sequence
                            if entry_quote is not None
                            else None
                        ),
                        entry_vwap=entry_vwap,
                        horizon_quote_received_at=(
                            horizon_quote.received_at if horizon_quote is not None else None
                        ),
                        horizon_receive_sequence=(
                            horizon_quote.position.receive_sequence
                            if horizon_quote is not None
                            else None
                        ),
                        horizon_vwap=horizon_vwap,
                        gross_return_bps=gross_return_bps,
                        reasons=tuple(dict.fromkeys(reasons)),
                    )
                )
    return tuple(labels)
