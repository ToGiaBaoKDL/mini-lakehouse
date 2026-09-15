import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from t0_trading.configuration import (
    TradingConfigurationError,
    load_configuration,
    parse_configuration,
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
    outcomes = configuration.resolve_outcomes(date(2026, 9, 5))
    assert outcomes.version == "top3-taker-markout-v1"
    assert outcomes.horizons_seconds == (30, 60, 300)
    assert outcomes.order_quantity == 100
    assert outcomes.execution_latency_milliseconds == 500
    strategies = configuration.resolve_strategies(date(2026, 9, 5))
    assert strategies.version == "microstructure-scores-v1"
    assert strategies.momentum_window_seconds == 60
    assert strategies.order_flow_window_seconds == 30
    assert strategies.relative_value_window_seconds == 300
    assert strategies.relative_value_symbols == ("VIC", "VHM")
    evaluation = configuration.resolve_strategy_evaluation(date(2026, 9, 5))
    assert evaluation.version == "purged-walk-forward-v1"
    assert evaluation.score_bucket_count == 5
    assert evaluation.minimum_training_sessions == 20
    assert evaluation.validation_sessions == 5
    assert evaluation.purge_sessions == 1
    decisions = configuration.resolve_decisions(date(2026, 9, 5))
    assert decisions.version == "microstructure-decisions-v1"
    assert decisions.strategy_version == strategies.version
    assert decisions.outcome_version == outcomes.version
    assert tuple(rule.strategy for rule in decisions.rules) == (
        "momentum",
        "order_flow",
        "relative_value",
    )
    assert decisions.rules[0].buy_minimum_strength == Decimal("0.68")
    assert decisions.rules[1].horizon_seconds == 300
    assert decisions.maximum_spread_bps == Decimal(25)
    assert decisions.maximum_trade_age_seconds == Decimal(30)
    assert decisions.maximum_quote_age_seconds == Decimal(5)
    assert decisions.cooldown_seconds == 60
    assert len(outcomes.sha256) == 64
    assert len(strategies.sha256) == 64
    assert len(evaluation.sha256) == 64
    assert len(decisions.sha256) == 64
    assert len(configuration.sha256) == 64
    assert len(version.sha256) == 64
    assert version.sha256 != configuration.sha256
    assert configuration.canonical_bytes() == configuration.canonical_bytes()
    assert parse_configuration(CONFIGURATION.read_text(encoding="utf-8")) == configuration


def test_trading_configuration_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = CONFIGURATION.read_text(encoding="utf-8").replace(
        "      quote_stale_after_seconds: 30",
        "      quote_stale_after_seconds: 30\n      threshold: 0.7",
    )
    path = tmp_path / "trading.yaml"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(TradingConfigurationError, match="invalid trading configuration"):
        load_configuration(path)


def test_outcome_assumptions_do_not_change_feature_configuration_identity() -> None:
    original = load_configuration(CONFIGURATION)
    changed = parse_configuration(
        CONFIGURATION.read_text(encoding="utf-8").replace(
            "order_quantity: 100",
            "order_quantity: 200",
        )
    )
    effective_date = date(2026, 9, 5)

    assert changed.resolve(effective_date).sha256 == original.resolve(effective_date).sha256
    assert (
        changed.resolve_outcomes(effective_date).sha256
        != original.resolve_outcomes(effective_date).sha256
    )


def test_strategy_assumptions_have_an_independent_identity() -> None:
    original = load_configuration(CONFIGURATION)
    changed = parse_configuration(
        CONFIGURATION.read_text(encoding="utf-8").replace(
            "momentum_window_seconds: 60",
            "momentum_window_seconds: 300",
        )
    )
    effective_date = date(2026, 9, 5)

    assert changed.resolve(effective_date).sha256 == original.resolve(effective_date).sha256
    assert (
        changed.resolve_outcomes(effective_date).sha256
        == original.resolve_outcomes(effective_date).sha256
    )
    assert (
        changed.resolve_strategies(effective_date).sha256
        != original.resolve_strategies(effective_date).sha256
    )


def test_strategy_evaluation_assumptions_have_an_independent_identity() -> None:
    original = load_configuration(CONFIGURATION)
    changed = parse_configuration(
        CONFIGURATION.read_text(encoding="utf-8").replace(
            "score_bucket_count: 5",
            "score_bucket_count: 10",
        )
    )
    effective_date = date(2026, 9, 5)

    assert changed.resolve(effective_date).sha256 == original.resolve(effective_date).sha256
    assert (
        changed.resolve_outcomes(effective_date).sha256
        == original.resolve_outcomes(effective_date).sha256
    )
    assert (
        changed.resolve_strategies(effective_date).sha256
        == original.resolve_strategies(effective_date).sha256
    )
    assert (
        changed.resolve_strategy_evaluation(effective_date).sha256
        != original.resolve_strategy_evaluation(effective_date).sha256
    )


def test_decision_assumptions_have_an_independent_identity() -> None:
    original = load_configuration(CONFIGURATION)
    changed = parse_configuration(
        CONFIGURATION.read_text(encoding="utf-8").replace(
            'buy_minimum_strength: "0.68"',
            'buy_minimum_strength: "0.70"',
        )
    )
    effective_date = date(2026, 9, 5)

    assert changed.resolve(effective_date).sha256 == original.resolve(effective_date).sha256
    assert (
        changed.resolve_strategies(effective_date).sha256
        == original.resolve_strategies(effective_date).sha256
    )
    assert (
        changed.resolve_decisions(effective_date).sha256
        != original.resolve_decisions(effective_date).sha256
    )


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
    payload = {
        "schema_version": 1,
        "versions": versions,
        "outcomes": load_configuration(CONFIGURATION).model_dump(mode="json")["outcomes"],
        "strategies": load_configuration(CONFIGURATION).model_dump(mode="json")["strategies"],
        "strategy_evaluations": load_configuration(CONFIGURATION).model_dump(mode="json")[
            "strategy_evaluations"
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
    with pytest.raises(TradingConfigurationError, match="found 0"):
        configuration.resolve_outcomes(date(2026, 8, 26))
    with pytest.raises(TradingConfigurationError, match="found 0"):
        configuration.resolve_strategies(date(2026, 8, 26))
    with pytest.raises(TradingConfigurationError, match="found 0"):
        configuration.resolve_strategy_evaluation(date(2026, 8, 26))


@pytest.mark.parametrize(
    ("original", "invalid"),
    (
        ("horizons_seconds: [30, 60, 300]", "horizons_seconds: [60, 30]"),
        ("order_quantity: 100", "order_quantity: 0"),
        ("execution_latency_milliseconds: 500", "execution_latency_milliseconds: 30000"),
    ),
)
def test_trading_configuration_rejects_invalid_outcome_policy(
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


@pytest.mark.parametrize(
    ("original", "invalid"),
    (
        ("momentum_window_seconds: 60", "momentum_window_seconds: 0"),
        ("relative_value_symbols: [VIC, VHM]", "relative_value_symbols: [VIC, VIC]"),
        ("relative_value_symbols: [VIC, VHM]", "relative_value_symbols: [vic, VHM]"),
    ),
)
def test_trading_configuration_rejects_invalid_strategy_policy(
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


@pytest.mark.parametrize(
    ("original", "invalid"),
    (
        ("score_bucket_count: 5", "score_bucket_count: 1"),
        ("minimum_training_sessions: 20", "minimum_training_sessions: 1"),
        ("validation_sessions: 5", "validation_sessions: 0"),
        ("purge_sessions: 1", "purge_sessions: 0"),
    ),
)
def test_trading_configuration_rejects_invalid_strategy_evaluation_policy(
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
