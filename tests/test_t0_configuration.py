import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from t0_trading.configuration import (
    TradingConfigurationError,
    load_configuration,
    parse_configuration,
)
from t0_trading.identity import sha256

CONFIGURATION = Path("t0-trading/config/trading.yaml")


@pytest.mark.parametrize(
    "collection",
    [
        "versions",
        "contexts",
        "outcomes",
        "breadth",
        "regimes",
        "candidate_arbitrations",
        "promotion_gates",
        "paper_executions",
        "baseline_evaluations",
    ],
)
def test_policy_closure_changes_resolution_metadata_not_historical_identity(
    collection: str,
) -> None:
    configuration = load_configuration(CONFIGURATION)
    policy = getattr(configuration, collection)[0]
    closed = policy.model_copy(update={"effective_to": policy.effective_from + timedelta(days=30)})
    assert closed.contains(policy.effective_from)
    assert not closed.contains(policy.effective_from + timedelta(days=31))
    assert closed.canonical_bytes() != policy.canonical_bytes()
    assert closed.sha256 == policy.sha256 == sha256(policy.canonical_bytes())
    assert closed.model_copy(update={"version": "changed-v2"}).sha256 != policy.sha256
    assert (
        closed.model_copy(
            update={"effective_from": policy.effective_from + timedelta(days=1)}
        ).sha256
        != policy.sha256
    )


def test_trading_configuration_is_strict_effective_dated_and_stable() -> None:
    configuration = load_configuration(CONFIGURATION)
    version = configuration.resolve(date(2026, 9, 5))

    assert configuration.capture.symbols == ("VIC", "VHM")
    assert configuration.capture.indices == ("VNINDEX", "VN30", "VNREAL")
    assert configuration.capture.membership_indices == ("VN30", "VNREAL")
    assert configuration.capture_scope(date(2026, 9, 5)) == configuration.capture
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
    baseline = configuration.resolve_baseline_evaluation(date(2026, 9, 5), "EXPLORATORY")
    assert baseline.version == "exploratory-baseline-holdout-v1"
    assert baseline.development_sessions == 10
    assert baseline.purge_sessions == 1
    assert baseline.holdout_sessions == 5
    promotion = configuration.resolve_baseline_evaluation(date(2026, 9, 5), "PROMOTION")
    assert promotion.version == "promotion-baseline-holdout-v1"
    assert promotion.development_sessions == 20
    assert configuration.resolve_candidate_arbitration(date(2026, 9, 27)) is None
    arbitration = configuration.resolve_candidate_arbitration(date(2026, 9, 28))
    assert arbitration is not None
    assert arbitration.version == "buy-first-candidate-arbitration-v1"
    assert arbitration.candidate_version == "buy-first-baselines-v3"
    assert arbitration.cooldown_seconds == 300
    assert arbitration.maximum_selections_per_clock == 2
    assert tuple((rule.strategy, rule.priority) for rule in arbitration.rules) == (
        ("vic_vhm_relative", 0),
        ("momentum_pullback", 1),
        ("mean_reversion", 2),
    )
    assert {rule.horizon_seconds for rule in arbitration.rules} == {300}
    assert configuration.resolve_promotion_gate(date(2026, 9, 27)) is None
    promotion_gate = configuration.resolve_promotion_gate(date(2026, 9, 28))
    assert promotion_gate is not None
    assert promotion_gate.version == "shadow-to-paper-v1"
    assert promotion_gate.scope == "SHADOW_TO_PAPER"
    assert promotion_gate.minimum_selected_count == 5
    assert promotion_gate.minimum_outcome_coverage_rate == Decimal("0.90")
    assert len(promotion_gate.targets) == 6
    assert configuration.resolve_paper_execution(date(2026, 9, 27)) is None
    paper_execution = configuration.resolve_paper_execution(date(2026, 9, 28))
    assert paper_execution is not None
    assert paper_execution.version == "buy-first-paper-execution-v1"
    assert paper_execution.promotion_gate_version == "shadow-to-paper-v1"
    assert paper_execution.context_version == "decision-context-v3"
    assert paper_execution.order_quantity == 100
    assert paper_execution.lot_size == 100
    assert paper_execution.maximum_quote_age_seconds == 5
    context = configuration.resolve_context(date(2026, 9, 5))
    assert context.version == "decision-context-v3"
    assert context.zone_lookback_seconds == 900
    assert context.market_windows_seconds == (60, 300)
    assert context.zone_tolerance_bps == Decimal(20)
    assert context.historical_proxy_interval_seconds == 60
    assert context.historical_proxy_stale_after_seconds == 65
    assert context.tradable_market_statuses == ("LO", "OPEN", "CONTINUOUS")
    assert len(context.sha256) == 64
    assert len(outcomes.sha256) == 64
    assert len(baseline.sha256) == 64
    assert len(configuration.sha256) == 64
    assert len(promotion_gate.sha256) == 64
    assert len(paper_execution.sha256) == 64
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


@pytest.mark.parametrize("schema_version", (1, 3))
def test_trading_configuration_rejects_unsupported_schema_version(
    schema_version: int,
) -> None:
    payload = CONFIGURATION.read_text(encoding="utf-8").replace(
        "schema_version: 2",
        f"schema_version: {schema_version}",
        1,
    )

    with pytest.raises(TradingConfigurationError, match="invalid trading configuration"):
        parse_configuration(payload)


def test_candidate_arbitration_horizon_requires_a_configured_outcome() -> None:
    payload = CONFIGURATION.read_text(encoding="utf-8").replace(
        "        horizon_seconds: 300",
        "        horizon_seconds: 120",
        1,
    )

    with pytest.raises(TradingConfigurationError, match="invalid trading configuration"):
        parse_configuration(payload)


@pytest.mark.parametrize(
    ("original", "invalid"),
    (
        (
            "evaluation_version: promotion-baseline-holdout-v1",
            "evaluation_version: missing-evaluation-v1",
        ),
        (
            "arbitration_version: buy-first-candidate-arbitration-v1",
            "arbitration_version: missing-arbitration-v1",
        ),
        (
            "order_quantity: 100\n    lot_size: 100",
            "order_quantity: 50\n    lot_size: 100",
        ),
    ),
)
def test_promotion_and_paper_policies_reject_broken_references(
    original: str,
    invalid: str,
) -> None:
    with pytest.raises(TradingConfigurationError, match="invalid trading configuration"):
        parse_configuration(CONFIGURATION.read_text(encoding="utf-8").replace(original, invalid, 1))


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


def test_capture_scope_has_independent_operational_identity() -> None:
    original = load_configuration(CONFIGURATION)
    changed = parse_configuration(
        CONFIGURATION.read_text(encoding="utf-8").replace(
            "indices: [VNINDEX, VN30, VNREAL]",
            "indices: [VNINDEX, VN30, VNREAL, VNFIN]",
            1,
        )
    )
    effective_date = date(2026, 9, 5)

    assert changed.capture.indices == ("VNINDEX", "VN30", "VNREAL", "VNFIN")
    assert changed.resolve(effective_date).sha256 == original.resolve(effective_date).sha256
    assert changed.sha256 != original.sha256


def test_capture_scope_must_cover_effective_decision_requirements() -> None:
    content = CONFIGURATION.read_text(encoding="utf-8").replace(
        "indices: [VNINDEX, VN30, VNREAL]",
        "indices: [VNINDEX, VNREAL]",
        1,
    )
    configuration = parse_configuration(
        content.replace("membership_indices: [VN30, VNREAL]", "membership_indices: [VNREAL]")
        .replace("    indices: [VN30, VNREAL]", "    indices: [VNREAL]")
        .replace("reference_index: VN30", "reference_index: VNREAL")
    )

    with pytest.raises(TradingConfigurationError, match="does not cover"):
        configuration.capture_scope(date(2026, 9, 5))


def test_context_assumptions_have_an_independent_identity() -> None:
    original = load_configuration(CONFIGURATION)
    changed = parse_configuration(
        CONFIGURATION.read_text(encoding="utf-8").replace(
            'zone_tolerance_bps: "20"',
            'zone_tolerance_bps: "25"',
        )
    )
    effective_date = date(2026, 9, 5)

    assert changed.resolve(effective_date).sha256 == original.resolve(effective_date).sha256
    assert (
        changed.resolve_context(effective_date).sha256
        != original.resolve_context(effective_date).sha256
    )


def test_baseline_evaluation_assumptions_have_an_independent_identity() -> None:
    original = load_configuration(CONFIGURATION)
    changed = parse_configuration(
        CONFIGURATION.read_text(encoding="utf-8").replace(
            "development_sessions: 20",
            "development_sessions: 25",
        )
    )
    effective_date = date(2026, 9, 5)

    assert changed.resolve(effective_date).sha256 == original.resolve(effective_date).sha256
    assert (
        changed.resolve_outcomes(effective_date).sha256
        == original.resolve_outcomes(effective_date).sha256
    )
    assert (
        changed.resolve_baseline_evaluation(effective_date).sha256
        != original.resolve_baseline_evaluation(effective_date).sha256
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
    payload = load_configuration(CONFIGURATION).model_dump(mode="json") | {"versions": versions}
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
        configuration.resolve_baseline_evaluation(date(2026, 8, 26))


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
        ("development_sessions: 20", "development_sessions: 1"),
        ("holdout_sessions: 5", "holdout_sessions: 0"),
        ("purge_sessions: 1", "purge_sessions: 0"),
    ),
)
def test_trading_configuration_rejects_invalid_baseline_evaluation_policy(
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
