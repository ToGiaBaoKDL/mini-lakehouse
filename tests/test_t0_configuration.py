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
    assert version.market.sessions.opening_auction[0].isoformat() == "09:00:00"
    assert version.market.sessions.closing_auction[1].isoformat() == "14:45:00"
    assert version.features.version == "microstructure-v1"
    assert version.features.cadence_seconds == 5
    assert version.features.windows_seconds == (30, 60, 300)
    assert version.features.warmup_seconds == 300
    assert version.features.decision_sessions == ("continuous_am", "continuous_pm")
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


@pytest.mark.parametrize(
    ("original", "invalid"),
    (
        ("cadence_seconds: 5", "cadence_seconds: 7"),
        ("windows_seconds: [30, 60, 300]", "windows_seconds: [60, 30, 300]"),
        ("warmup_seconds: 300", "warmup_seconds: 60"),
        (
            "decision_sessions: [continuous_am, continuous_pm]",
            "decision_sessions: [continuous_pm, continuous_am]",
        ),
    ),
)
def test_trading_configuration_rejects_invalid_feature_policy(
    tmp_path: Path,
    original: str,
    invalid: str,
) -> None:
    path = tmp_path / "trading.yaml"
    path.write_text(
        CONFIGURATION.read_text(encoding="utf-8").replace(original, invalid),
        encoding="utf-8",
    )

    with pytest.raises(TradingConfigurationError, match="invalid trading configuration"):
        load_configuration(path)


def test_trading_configuration_rejects_overlapping_versions(tmp_path: Path) -> None:
    base = load_configuration(CONFIGURATION).versions[0].model_dump(mode="json")
    versions: list[object] = []
    for version, effective_from, effective_to in (
        ("one", "2026-01-01", "2026-06-30"),
        ("two", "2026-06-30", None),
    ):
        versions.append(
            base
            | {
                "version": version,
                "effective_from": effective_from,
                "effective_to": effective_to,
            }
        )
    payload = {"schema_version": 1, "versions": versions}
    path = tmp_path / "trading.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TradingConfigurationError, match="invalid trading configuration"):
        load_configuration(path)


def test_trading_configuration_fails_closed_for_unconfigured_date() -> None:
    configuration = load_configuration(CONFIGURATION)

    with pytest.raises(TradingConfigurationError, match="found 0"):
        configuration.resolve(date(2026, 8, 26))
