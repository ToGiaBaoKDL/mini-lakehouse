import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from t0_trading.configuration import load_configuration
from t0_trading.features import FeatureSnapshot
from t0_trading.market import StreamEnvelope
from t0_trading.market.session import MarketSession
from t0_trading.outcomes import label_outcomes

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = date(2026, 9, 4)
MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def _configuration():
    configuration = load_configuration(CONFIGURATION)
    return configuration.resolve(TRADE_DATE), configuration.resolve_outcomes(TRADE_DATE)


def _received(hour: int, minute: int, second: int, microsecond: int = 0) -> datetime:
    return datetime(2026, 9, 4, hour - 7, minute, second, microsecond, tzinfo=UTC)


def _snapshot(
    decision_at: datetime,
    *,
    reasons: tuple[str, ...] = (),
    symbol: str = "VIC",
    market_session: MarketSession = MarketSession.CONTINUOUS_AM,
) -> FeatureSnapshot:
    configuration, _ = _configuration()
    return FeatureSnapshot(
        feature_version=configuration.features.version,
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        symbol=symbol,
        trade_date=TRADE_DATE,
        decision_at=decision_at,
        market_session=market_session,
        stream_session_id="session-1",
        last_receive_sequence=1,
        trade_age_seconds=Decimal(1),
        quote_age_seconds=Decimal(1),
        mid_price=Decimal("100.5"),
        microprice=Decimal("100.5"),
        microprice_deviation_bps=Decimal(0),
        spread=Decimal(1),
        spread_bps=Decimal("99.5025"),
        bid_depth=180,
        ask_depth=180,
        level_one_imbalance=Decimal(0),
        depth_imbalance=Decimal(0),
        windows=(),
        reasons=reasons,
    )


def _quote(
    sequence: int,
    *,
    received_at: datetime,
    bid: int,
    ask: int,
    quantities: tuple[int, int, int] = (60, 60, 60),
    complete: bool = True,
    source_time: datetime | None = None,
) -> StreamEnvelope:
    active_quantities = quantities if complete else (quantities[0], 0, 0)
    bid_prices = (bid, bid - 1, bid - 2) if complete else (bid, 0, 0)
    ask_prices = (ask, ask + 1, ask + 2) if complete else (ask, 0, 0)
    payload = {
        "type": "quote",
        "symbol": "VIC",
        "trading_time": (source_time or received_at)
        .astimezone(MARKET_TIMEZONE)
        .strftime("%Y/%m/%d %H:%M:%S"),
        "bid_prices": [*bid_prices, *([0] * 7)],
        "bid_volumes": [*active_quantities, *([0] * 7)],
        "ask_prices": [*ask_prices, *([0] * 7)],
        "ask_volumes": [*active_quantities, *([0] * 7)],
    }
    message_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return StreamEnvelope(
        stream_session_id="session-1",
        receive_sequence=sequence,
        message_type="QuoteMessage",
        symbol="VIC",
        source_time_text=str(payload["trading_time"]),
        received_at=received_at,
        message_json=message_json,
        message_sha256=hashlib.sha256(message_json.encode()).hexdigest(),
    )


def _base_quotes() -> tuple[StreamEnvelope, ...]:
    return (
        _quote(1, received_at=_received(9, 20, 0), bid=100, ask=101),
        _quote(
            2,
            received_at=_received(9, 20, 30),
            bid=103,
            ask=104,
            quantities=(100, 100, 100),
        ),
    )


def test_top_three_book_walk_and_directional_markouts_are_exact() -> None:
    configuration, policy = _configuration()
    snapshot = _snapshot(_received(9, 20, 0))

    labels = label_outcomes((snapshot,), _base_quotes(), configuration, policy)
    buy = next(label for label in labels if label.action == "BUY" and label.horizon_seconds == 30)
    sell = next(label for label in labels if label.action == "SELL" and label.horizon_seconds == 30)

    assert len(labels) == 6
    assert len({label.sha256 for label in labels}) == 6
    assert buy.is_eligible is True
    assert buy.entry_at == _received(9, 20, 0, 500_000)
    assert buy.entry_receive_sequence == 1
    assert buy.horizon_receive_sequence == 2
    assert buy.entry_vwap == Decimal("101.40000000")
    assert buy.horizon_vwap == Decimal("103.00000000")
    assert buy.gross_return_bps == Decimal("157.7909")
    assert sell.entry_vwap == Decimal("99.60000000")
    assert sell.horizon_vwap == Decimal("104.00000000")
    assert sell.gross_return_bps == Decimal("-441.7671")


def test_future_quote_cannot_change_an_earlier_outcome() -> None:
    configuration, policy = _configuration()
    snapshot = _snapshot(_received(9, 20, 0))
    base = label_outcomes((snapshot,), _base_quotes(), configuration, policy)
    future = _quote(3, received_at=_received(9, 20, 31), bid=900, ask=901)
    repeated = label_outcomes(
        (snapshot,),
        (*_base_quotes(), future),
        configuration,
        policy,
    )

    assert tuple(label for label in base if label.horizon_seconds == 30) == tuple(
        label for label in repeated if label.horizon_seconds == 30
    )


def test_latest_incomplete_book_supersedes_an_older_complete_quote() -> None:
    configuration, policy = _configuration()
    snapshot = _snapshot(_received(9, 20, 0))
    quotes = (
        _quote(1, received_at=_received(9, 20, 0), bid=100, ask=101),
        _quote(
            2,
            received_at=_received(9, 20, 0, 250_000),
            bid=100,
            ask=101,
            complete=False,
        ),
        _quote(3, received_at=_received(9, 20, 30), bid=103, ask=104),
    )

    label = next(
        item
        for item in label_outcomes((snapshot,), quotes, configuration, policy)
        if item.action == "BUY" and item.horizon_seconds == 30
    )

    assert label.entry_receive_sequence == 2
    assert label.entry_vwap is None
    assert label.gross_return_bps is None
    assert label.reasons == ("INCOMPLETE_ENTRY_BOOK",)


def test_stale_depth_and_session_boundaries_fail_closed() -> None:
    configuration, policy = _configuration()
    snapshot = _snapshot(_received(9, 20, 0))
    shallow = (
        _quote(1, received_at=_received(9, 20, 0), bid=100, ask=101, quantities=(30, 30, 30)),
    )
    labels = label_outcomes((snapshot,), shallow, configuration, policy)

    thirty = next(item for item in labels if item.action == "BUY" and item.horizon_seconds == 30)
    sixty = next(item for item in labels if item.action == "BUY" and item.horizon_seconds == 60)
    assert thirty.reasons == ("INSUFFICIENT_ENTRY_DEPTH", "INSUFFICIENT_HORIZON_DEPTH")
    assert sixty.reasons == ("INSUFFICIENT_ENTRY_DEPTH", "STALE_HORIZON_QUOTE")

    closing = _snapshot(_received(11, 29, 55))
    outside = label_outcomes((closing,), (), configuration, policy)
    assert all("HORIZON_OUTSIDE_SESSION" in item.reasons for item in outside)
    assert all(item.gross_return_bps is None for item in outside)


def test_feature_eligibility_and_input_lineage_are_preserved() -> None:
    configuration, policy = _configuration()
    snapshot = _snapshot(_received(9, 20, 0), reasons=("STALE_QUOTE",))
    labels = label_outcomes((snapshot,), _base_quotes(), configuration, policy)

    thirty = next(item for item in labels if item.action == "BUY" and item.horizon_seconds == 30)
    assert thirty.gross_return_bps == Decimal("157.7909")
    assert thirty.reasons == ("FEATURE_INELIGIBLE",)
    assert thirty.is_eligible is False

    with pytest.raises(ValueError, match="SEQUENCE_GAP"):
        label_outcomes(
            (snapshot,),
            (_quote(2, received_at=_received(9, 20, 0), bid=100, ask=101),),
            configuration,
            policy,
        )

    with pytest.raises(ValueError, match="configuration lineage"):
        label_outcomes(
            (_snapshot(_received(9, 20, 0), symbol="HPG"),),
            _base_quotes(),
            configuration,
            policy,
        )

    with pytest.raises(ValueError, match="configuration lineage"):
        label_outcomes(
            (
                _snapshot(
                    _received(9, 20, 0),
                    market_session=MarketSession.CONTINUOUS_PM,
                ),
            ),
            _base_quotes(),
            configuration,
            policy,
        )


def test_outcomes_reuse_market_integrity_and_source_time_freshness() -> None:
    configuration, policy = _configuration()
    snapshot = _snapshot(_received(9, 20, 0))
    regressed = (
        _quote(1, received_at=_received(9, 20, 0), bid=100, ask=101),
        _quote(
            2,
            received_at=_received(9, 20, 30),
            source_time=_received(9, 19, 59),
            bid=103,
            ask=104,
        ),
    )

    with pytest.raises(ValueError, match="QUOTE_TIME_REGRESSION"):
        label_outcomes((snapshot,), regressed, configuration, policy)

    delayed = (
        _quote(
            1,
            received_at=_received(9, 20, 0),
            source_time=_received(9, 19, 0),
            bid=100,
            ask=101,
        ),
        _quote(
            2,
            received_at=_received(9, 20, 30),
            source_time=_received(9, 19, 30),
            bid=103,
            ask=104,
        ),
    )
    label = next(
        item
        for item in label_outcomes((snapshot,), delayed, configuration, policy)
        if item.action == "BUY" and item.horizon_seconds == 30
    )

    assert label.reasons == ("STALE_ENTRY_QUOTE", "STALE_HORIZON_QUOTE")
