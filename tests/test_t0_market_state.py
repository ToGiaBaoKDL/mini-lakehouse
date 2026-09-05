import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from t0_trading.configuration import load_configuration
from t0_trading.market import MarketState, StreamEnvelope, replay
from t0_trading.market.events import AuctionObservation, MarketEventError, QuoteSnapshot
from t0_trading.market.state import LATE_TRADE, SEQUENCE_GAP, TRADE_TIME_REGRESSION


def _configuration():
    return load_configuration(Path("t0-trading/config/trading.yaml")).resolve(date(2026, 9, 5))


def _envelope(
    sequence: int,
    message_type: str,
    payload: dict[str, object],
    *,
    received_at: datetime | None = None,
    session: str = "session-1",
) -> StreamEnvelope:
    message_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    raw_symbol = payload.get("symbol")
    raw_source_time = payload.get("trading_time")
    return StreamEnvelope(
        stream_session_id=session,
        receive_sequence=sequence,
        message_type=message_type,
        symbol=raw_symbol if isinstance(raw_symbol, str) else None,
        source_time_text=raw_source_time if isinstance(raw_source_time, str) else None,
        received_at=received_at or datetime(2026, 9, 4, 2, 0, sequence, tzinfo=UTC),
        message_json=message_json,
        message_sha256=hashlib.sha256(message_json.encode()).hexdigest(),
    )


def _trade(
    sequence: int,
    trading_time: str,
    price: int | float,
    quantity: int,
    side: str,
) -> StreamEnvelope:
    return _envelope(
        sequence,
        "TradeMessage",
        {
            "type": "trade",
            "symbol": "VIC",
            "trading_time": trading_time,
            "price": price,
            "quantity": quantity,
            "side": side,
            "total_volume": 1_000 + quantity,
        },
        received_at=datetime(2026, 9, 4, 2, 2, sequence, tzinfo=UTC),
    )


def _quote(sequence: int, *, complete: bool = True) -> StreamEnvelope:
    bid_prices = [100, 99, 98] if complete else [100, 0, 0]
    bid_volumes = [10, 20, 30] if complete else [10, 0, 0]
    ask_prices = [101, 102, 103] if complete else [101, 0, 0]
    ask_volumes = [11, 21, 31] if complete else [11, 0, 0]
    return _envelope(
        sequence,
        "QuoteMessage",
        {
            "type": "quote",
            "symbol": "VIC",
            "trading_time": "2026/09/04 09:02:00",
            "bid_prices": bid_prices + [0] * 7,
            "bid_volumes": bid_volumes + [0] * 7,
            "ask_prices": ask_prices + [0] * 7,
            "ask_volumes": ask_volumes + [0] * 7,
        },
        received_at=datetime(2026, 9, 4, 2, 2, sequence, tzinfo=UTC),
    )


def test_market_state_builds_exact_trade_bar() -> None:
    state = MarketState(_configuration())
    state.apply(_trade(1, "2026/09/04 09:00:05", 100, 10, "B"))
    state.apply(_trade(2, "2026/09/04 09:00:40", 102, 20, "S"))
    update = state.apply(_trade(3, "2026/09/04 09:01:01", 101, 5, "B"))

    assert len(update.finalized_bars) == 1
    bar = update.finalized_bars[0]
    assert (bar.open_price, bar.high_price, bar.low_price, bar.close_price) == (
        Decimal(100),
        Decimal(102),
        Decimal(100),
        Decimal(102),
    )
    assert bar.volume == 30
    assert bar.value == Decimal(3040)
    assert bar.vwap == Decimal("101.33333333")
    assert (bar.trade_count, bar.buy_volume, bar.sell_volume) == (2, 10, 20)


def test_explicit_clock_finalizes_last_bar_without_synthetic_empty_bars() -> None:
    state = MarketState(_configuration())
    state.apply(_trade(1, "2026/09/04 09:00:05", 100, 10, "B"))

    bars = state.advance(
        datetime(2026, 9, 4, 2, 1, tzinfo=UTC),
        available_at=datetime(2026, 9, 4, 2, 1, 1, tzinfo=UTC),
    )
    assert len(bars) == 1
    assert (
        state.advance(
            datetime(2026, 9, 4, 2, 2, tzinfo=UTC),
            available_at=datetime(2026, 9, 4, 2, 2, tzinfo=UTC),
        )
        == ()
    )


def test_trade_for_a_finalized_interval_is_rejected_as_late() -> None:
    state = MarketState(_configuration())
    state.apply(_trade(1, "2026/09/04 09:00:05", 100, 10, "B"))
    state.advance(
        datetime(2026, 9, 4, 2, 1, tzinfo=UTC),
        available_at=datetime(2026, 9, 4, 2, 1, 1, tzinfo=UTC),
    )

    update = state.apply(_trade(2, "2026/09/04 09:00:50", 101, 5, "S"))

    assert update.event is None
    assert update.issues == (LATE_TRADE,)
    assert (
        state.advance(
            datetime(2026, 9, 4, 2, 2, tzinfo=UTC),
            available_at=datetime(2026, 9, 4, 2, 2, tzinfo=UTC),
        )
        == ()
    )


def test_auction_observation_does_not_create_a_trade_bar() -> None:
    state = MarketState(_configuration())
    update = state.apply(_trade(1, "2026/09/04 09:00:00", 0, 0, "U"))

    assert isinstance(update.event, AuctionObservation)
    assert (
        state.advance(
            datetime(2026, 9, 4, 2, 1, tzinfo=UTC),
            available_at=datetime(2026, 9, 4, 2, 1, tzinfo=UTC),
        )
        == ()
    )


def test_quote_completeness_and_freshness_fail_closed() -> None:
    state = MarketState(_configuration())
    update = state.apply(_quote(1, complete=False))

    assert isinstance(update.event, QuoteSnapshot)
    assert update.event.is_complete is False
    health = state.health(
        "VIC",
        evaluated_at=datetime(2026, 9, 4, 2, 2, 10, tzinfo=UTC),
    )
    assert health.ready is False
    assert health.reasons == ("MISSING_TRADE", "INCOMPLETE_QUOTE")

    stale = state.health(
        "VIC",
        evaluated_at=datetime(2026, 9, 4, 2, 3, tzinfo=UTC),
    )
    assert "STALE_QUOTE" in stale.reasons


def test_complete_current_trade_and_quote_state_is_ready() -> None:
    state = MarketState(_configuration())
    state.apply(_trade(1, "2026/09/04 09:02:00", 100, 10, "B"))
    state.apply(_quote(2))

    health = state.health(
        "VIC",
        evaluated_at=datetime(2026, 9, 4, 2, 2, 10, tzinfo=UTC),
    )
    assert health.ready is True
    assert health.reasons == ()


def test_sequence_gap_and_event_time_regression_are_preserved_as_integrity_issues() -> None:
    state = MarketState(_configuration())
    state.apply(_trade(1, "2026/09/04 09:01:00", 100, 10, "B"))
    update = state.apply(_trade(3, "2026/09/04 09:00:00", 101, 10, "B"))

    assert update.issues == (SEQUENCE_GAP, TRADE_TIME_REGRESSION)
    assert state.integrity_issues == (SEQUENCE_GAP, TRADE_TIME_REGRESSION)


def test_replay_and_incremental_processing_use_identical_state_transitions() -> None:
    configuration = _configuration()
    envelopes = (
        _trade(1, "2026/09/04 09:00:05", 100, 10, "B"),
        _quote(2),
        _trade(3, "2026/09/04 09:01:05", 101, 20, "S"),
    )
    finish_at = datetime(2026, 9, 4, 2, 2, 10, tzinfo=UTC)

    incremental = MarketState(configuration)
    incremental_bars = []
    for envelope in envelopes:
        incremental_bars.extend(incremental.apply(envelope).finalized_bars)
    incremental_bars.extend(incremental.advance(finish_at, available_at=finish_at))
    replayed = replay(envelopes, configuration, finish_at=finish_at)

    assert replayed.finalized_bars == tuple(incremental_bars)
    assert replayed.input_count == 3
    assert replayed.business_event_count == 3
    assert replayed.issues == ()
    assert replayed.state.latest_quote("VIC") == incremental.latest_quote("VIC")
    assert replayed.state.integrity_issues == incremental.integrity_issues


def test_fractional_price_is_rejected_at_the_source_boundary() -> None:
    state = MarketState(_configuration())

    with pytest.raises(MarketEventError, match="finite whole VND"):
        state.apply(_trade(1, "2026/09/04 09:00:05", 100.5, 10, "B"))


def test_stale_trade_and_quote_are_calculated_from_receipt_time() -> None:
    configuration = _configuration()
    state = MarketState(configuration)
    state.apply(_trade(1, "2026/09/04 09:02:00", 100, 10, "B"))
    state.apply(_quote(2))

    healthy = state.health(
        "VIC",
        evaluated_at=datetime(2026, 9, 4, 2, 2, 20, tzinfo=UTC),
    )
    assert healthy.ready is True
    assert healthy.reasons == ()

    stale = state.health(
        "VIC",
        evaluated_at=datetime(2026, 9, 4, 2, 4, tzinfo=UTC) + timedelta(seconds=1),
    )
    assert stale.ready is False
    assert set(stale.reasons) == {"STALE_TRADE", "STALE_QUOTE"}


def test_health_rejects_state_that_was_not_available_at_evaluation_time() -> None:
    state = MarketState(_configuration())
    state.apply(_trade(1, "2026/09/04 09:02:00", 100, 10, "B"))
    state.apply(_quote(2))

    health = state.health(
        "VIC",
        evaluated_at=datetime(2026, 9, 4, 2, 2, tzinfo=UTC),
    )
    assert health.ready is False
    assert set(health.reasons) == {"FUTURE_TRADE", "FUTURE_QUOTE"}


def test_replay_rejects_finish_time_before_captured_input() -> None:
    envelope = _trade(1, "2026/09/04 09:02:00", 100, 10, "B")

    with pytest.raises(ValueError, match="last captured receipt"):
        replay(
            (envelope,),
            _configuration(),
            finish_at=envelope.received_at - timedelta(seconds=1),
        )
