import json
from datetime import date
from pathlib import Path

import pytest
from t0_trading.configuration import (
    TradingConfigurationError,
    load_configuration,
)

CONFIGURATION = Path("t0-trading/config/trading.yaml")


def test_trading_configuration_is_strict_effective_dated_and_stable() -> None:
    configuration = load_configuration(CONFIGURATION)
    version = configuration.resolve(date(2026, 9, 5))

    assert version.version == "market-state-v1"
    assert version.market.symbols == ("VIC", "VHM")
    assert version.market.indices == ("VNINDEX", "VN30")
    assert version.market.quote_depth == 3
    assert version.market.bar_interval_seconds == 60
    assert len(configuration.sha256) == 64
    assert len(version.sha256) == 64
    assert version.sha256 != configuration.sha256
    assert configuration.canonical_bytes() == configuration.canonical_bytes()


def test_trading_configuration_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = CONFIGURATION.read_text(encoding="utf-8").replace(
        "      quote_stale_after_seconds: 30",
        "      quote_stale_after_seconds: 30\n      threshold: 0.7",
    )
    path = tmp_path / "trading.yaml"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(TradingConfigurationError, match="invalid trading configuration"):
        load_configuration(path)


def test_trading_configuration_rejects_overlapping_versions(tmp_path: Path) -> None:
    payload = {
        "schema_version": 1,
        "versions": [
            {
                "version": version,
                "effective_from": effective_from,
                "effective_to": effective_to,
                "market": {
                    "timezone": "Asia/Ho_Chi_Minh",
                    "symbols": ["VIC", "VHM"],
                    "indices": ["VNINDEX", "VN30"],
                    "quote_depth": 3,
                    "bar_interval_seconds": 60,
                },
                "data_quality": {
                    "trade_stale_after_seconds": 90,
                    "quote_stale_after_seconds": 30,
                },
            }
            for version, effective_from, effective_to in (
                ("one", "2026-01-01", "2026-06-30"),
                ("two", "2026-06-30", None),
            )
        ],
    }
    path = tmp_path / "trading.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TradingConfigurationError, match="invalid trading configuration"):
        load_configuration(path)


def test_trading_configuration_fails_closed_for_unconfigured_date() -> None:
    configuration = load_configuration(CONFIGURATION)

    with pytest.raises(TradingConfigurationError, match="found 0"):
        configuration.resolve(date(2026, 8, 26))
