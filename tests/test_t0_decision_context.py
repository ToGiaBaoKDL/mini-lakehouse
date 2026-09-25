"""Decision context must be reproducible and strictly point-in-time."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from t0_trading.configuration import load_configuration
from t0_trading.context import build_decision_contexts
from t0_trading.features import FeatureSnapshot, WindowFeatures
from t0_trading.market.events import StreamEnvelope
from t0_trading.market.session import MarketSession
from t0_trading.strategy.baselines import score_buy_first_baselines

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = datetime(2026, 9, 21, tzinfo=UTC).date()
DECISION_AT = datetime(2026, 9, 21, 2, 30, tzinfo=UTC)


def _window(seconds: int, movement: str = "10") -> WindowFeatures:
    return WindowFeatures(
        window_seconds=seconds,
        trade_count=3,
        quote_change_count=1,
        trade_volume=1000,
        signed_trade_volume=600,
        trade_volume_per_second=Decimal(1000) / seconds,
        trade_volume_imbalance=Decimal("0.6"),
        level_one_order_flow_imbalance=100,
        price_return_bps=Decimal(movement),
        realized_volatility_bps=Decimal(20),
        vwap=Decimal(101),
        last_price_to_vwap_bps=Decimal(-20),
    )


def _snapshot(symbol: str, decision_at: datetime, mid_price: str) -> FeatureSnapshot:
    configuration = load_configuration(CONFIGURATION).resolve(TRADE_DATE)
    mid = Decimal(mid_price)
    return FeatureSnapshot(
        feature_version=configuration.features.version,
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        symbol=symbol,
        trade_date=TRADE_DATE,
        decision_at=decision_at,
        market_session=MarketSession.CONTINUOUS_AM,
        stream_session_id="session-1",
        last_receive_sequence=10,
        trade_age_seconds=Decimal(1),
        quote_age_seconds=Decimal(1),
        mid_price=mid,
        microprice=mid,
        microprice_deviation_bps=Decimal(0),
        spread=Decimal(1),
        spread_bps=Decimal(100),
        bid_depth=600,
        ask_depth=400,
        level_one_imbalance=Decimal("0.2"),
        depth_imbalance=Decimal("0.1"),
        windows=(_window(30), _window(60, "20"), _window(300, "30")),
        reasons=(),
    )


def _index_tick(sequence: int, index: str, at: datetime, value: str) -> StreamEnvelope:
    local = at + timedelta(hours=7)
    payload = {
        "type": "trade",
        "symbol": index,
        "trading_time": local.strftime("%Y/%m/%d %H:%M:%S"),
        "price": value,
        "quantity": 0,
        "side": "U",
        "total_volume": 0,
    }
    message_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return StreamEnvelope(
        stream_session_id="session-1",
        receive_sequence=sequence,
        message_type="TradeMessage",
        subscription_context="indices",
        symbol=index,
        source_time_text=str(payload["trading_time"]),
        received_at=at,
        message_json=message_json,
        message_sha256=hashlib.sha256(message_json.encode()).hexdigest(),
    )


def _snapshots() -> tuple[FeatureSnapshot, ...]:
    rows: list[FeatureSnapshot] = []
    for offset in range(60, 0, -5):
        at = DECISION_AT - timedelta(seconds=offset)
        rows.extend((_snapshot("VIC", at, "100"), _snapshot("VHM", at, "100")))
    rows.extend((_snapshot("VIC", DECISION_AT, "100"), _snapshot("VHM", DECISION_AT, "100")))
    return tuple(rows)


def _indices() -> tuple[StreamEnvelope, ...]:
    observations = (
        ("VN30", DECISION_AT - timedelta(seconds=301), "1200"),
        ("VNINDEX", DECISION_AT - timedelta(seconds=301), "1000"),
        ("VN30", DECISION_AT - timedelta(seconds=61), "1201"),
        ("VNINDEX", DECISION_AT - timedelta(seconds=61), "1001"),
        ("VN30", DECISION_AT - timedelta(seconds=1), "1203"),
        ("VNINDEX", DECISION_AT - timedelta(seconds=1), "1002"),
    )
    return tuple(
        _index_tick(sequence, index, at, value)
        for sequence, (index, at, value) in enumerate(observations, start=1)
    )


def test_context_adds_certified_zone_market_confirmation_and_regime() -> None:
    configuration = load_configuration(CONFIGURATION)
    version = configuration.resolve(TRADE_DATE)
    contexts = build_decision_contexts(
        _snapshots(), _indices(), version, configuration.resolve_context(TRADE_DATE)
    )

    current = contexts[-1]
    assert current.regime == "TREND_UP"
    confirmation = current.market_confirmation_strength
    assert confirmation is not None
    assert confirmation > 0
    assert not current.reasons
    assert all(zone.is_available and zone.near_support_strength == 1 for zone in current.zones)
    assert all(item.is_eligible for item in current.indices)
    assert type(current).model_validate_json(current.model_dump_json()) == current
    assert current.sha256 == contexts[-1].sha256

    candidates = score_buy_first_baselines(_snapshots(), contexts)
    mean = next(
        item
        for item in candidates
        if item.symbol == "VIC"
        and item.decision_at == DECISION_AT
        and item.strategy == "mean_reversion"
    )
    relative = next(
        item
        for item in candidates
        if item.symbol == "VIC"
        and item.decision_at == DECISION_AT
        and item.strategy == "vic_vhm_relative"
    )
    assert mean.groups[1].status == "MATCH"
    assert relative.groups[2].status == "MATCH"
    assert mean.market_regime == "TREND_UP"
    assert mean.context_snapshot_sha256 == current.sha256


def test_context_ignores_future_index_ticks_and_fails_closed_without_indices() -> None:
    configuration = load_configuration(CONFIGURATION)
    version = configuration.resolve(TRADE_DATE)
    policy = configuration.resolve_context(TRADE_DATE)
    snapshots = _snapshots()
    expected = build_decision_contexts(snapshots, _indices(), version, policy)
    future = _index_tick(7, "VN30", DECISION_AT + timedelta(seconds=1), "1300")
    repeated = build_decision_contexts(snapshots, (*_indices(), future), version, policy)

    assert repeated[-1] == expected[-1]
    missing = build_decision_contexts(snapshots, (), version, policy)[-1]
    assert missing.regime == "UNKNOWN"
    assert missing.market_confirmation_strength is None
    assert missing.reasons == ("VN30_MISSING_INDEX", "VNINDEX_MISSING_INDEX")
    assert all(zone.is_available for zone in missing.zones)
    candidates = score_buy_first_baselines(
        snapshots, build_decision_contexts(snapshots, (), version, policy)
    )
    relative = next(
        item
        for item in candidates
        if item.symbol == "VIC"
        and item.decision_at == DECISION_AT
        and item.strategy == "vic_vhm_relative"
    )
    assert relative.block_reasons == ("required_group_unavailable",)
