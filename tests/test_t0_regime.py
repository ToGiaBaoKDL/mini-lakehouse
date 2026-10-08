"""Source-aware analytical context must not weaken point-in-time authorization."""
# pyright: reportPrivateUsage=false

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from emr_jobs.t0_trading.research import _context_rows, _contexts
from t0_trading.arbitration.engine import CandidateArbitrator
from t0_trading.arbitration.journal import ShadowArbitrationJournal
from t0_trading.arbitration.selection import RealtimeSelection
from t0_trading.configuration import RegimeVersion, TradingConfiguration, load_configuration
from t0_trading.context import LiveDecisionContextEngine, build_decision_contexts
from t0_trading.context.model import DecisionContext
from t0_trading.context.regime import context_identity, market_state
from t0_trading.features import FeatureSnapshot
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.events import StreamEnvelope
from t0_trading.operations import operational_status
from t0_trading.strategy.baselines import score_buy_first_baselines
from test_t0_breadth import AT, _engine, _evidence, _interval, _membership, _policy
from test_t0_decision_context import _snapshot

from lakehouse.contracts import load_contracts

CONFIG = Path("t0-trading/config/trading.yaml")


def _regime(**updates: object) -> RegimeVersion:
    """Use the prospective defaults at an isolated unit-test clock, not stored evidence."""
    policy = load_configuration(CONFIG).resolve_regime(date(2026, 10, 9))
    assert policy is not None
    return RegimeVersion.model_validate(
        {**policy.model_dump(), "effective_from": AT.date(), **updates}
    )


def _snapshots(at: datetime = AT):
    return tuple(
        _snapshot(symbol, at, "100").model_copy(update={"trade_date": at.date()})
        for symbol in ("VHM", "VIC")
    )


def _live() -> LiveDecisionContextEngine:
    configuration = load_configuration(CONFIG)
    return LiveDecisionContextEngine(
        configuration.resolve(AT.date()),
        configuration.resolve_context(AT.date()),
        regime_policy=_regime(),
        breadth_policy=_policy(),
        breadth_membership=_membership(),
    )


def _status(
    status: str, at: datetime = AT - timedelta(minutes=7), session: str = "segment-1"
) -> StreamEnvelope:
    body = canonical_json(
        {"market": "HOSE", "status": status, "trading_date": AT.strftime("%d/%m/%Y")}
    ).decode()
    return StreamEnvelope(
        stream_session_id=session,
        receive_sequence=20,
        message_type="MarketStatusMessage",
        subscription_context="markets",
        symbol="HOSE",
        source_time_text=AT.strftime("%d/%m/%Y"),
        received_at=at,
        message_json=body,
        message_sha256=sha256(body.encode()),
    )


def test_prospective_basis_preserves_historical_policy_and_canonical_payload() -> None:
    configuration = load_configuration(CONFIG)
    old = configuration.resolve_context(date(2026, 10, 8))
    assert configuration.resolve_regime(date(2026, 10, 8)) is None
    assert configuration.resolve_regime(date(2026, 10, 9)) is not None
    assert context_identity(old, None, _policy()) == old.sha256
    assert context_identity(old, _regime(), _policy()) != old.sha256
    context = build_decision_contexts(_snapshots(), (), configuration.resolve(AT.date()), old)[0]
    assert "market_basis" not in context.model_dump(mode="json")
    assert "regime_policy_sha256" not in context.model_dump(mode="json")


def test_known_breadth_regime_does_not_authorize_missing_market_status() -> None:
    engine = _live()
    for envelope in _evidence():
        engine.apply(envelope)
    snapshots = _snapshots()
    context = engine.build(snapshots)
    assert context.regime == "TREND_UP"
    assert context.reasons == ()
    assert context.market_basis == "CONSTITUENT_BREADTH"
    assert context.market_reference_index == "VN30"
    assert context.market_confirmation_strength == Decimal("0.66666667")
    assert context.data_mode == "LIVE"
    assert not context.is_tradable
    assert all(item.value is None for item in context.indices)
    assert all(
        item.block_reasons == ("market_status_ineligible",)
        for item in score_buy_first_baselines(snapshots, (context,))
    )
    assert DecisionContext.model_validate_json(context.canonical_bytes()) == context


def test_unknown_required_regime_blocks_before_arbitration_and_recovers_without_cooldown() -> None:
    engine = _live()
    engine.apply(_status("LO"))
    context = engine.build(_snapshots())
    assert context.is_tradable and context.regime == "UNKNOWN"
    candidates = score_buy_first_baselines(_snapshots(), (context,))
    assert all(item.block_reasons == ("regime_ineligible",) for item in candidates)
    policy = load_configuration(CONFIG).resolve_candidate_arbitration(AT.date())
    assert policy is not None
    arbitrator = CandidateArbitrator(policy)
    assert all(
        item.rejection_reason == "UPSTREAM_BLOCKED" for item in arbitrator.decide(candidates)
    )
    for envelope in _evidence():
        engine.apply(envelope)
    snapshots = _snapshots(AT + timedelta(seconds=5))
    recovered = engine.build(snapshots)
    candidates = score_buy_first_baselines(snapshots, (recovered,))
    selected = tuple(item for item in arbitrator.decide(candidates) if item.status == "SELECTED")
    assert selected
    by_sha = {item.sha256: item for item in candidates}
    for arbitration in selected:
        RealtimeSelection(
            candidate=by_sha[arbitration.candidate_sha256],
            arbitration=arbitration,
            context=recovered,
            features=snapshots,
        )


def test_journal_keeps_handoff_enabled_when_required_regime_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_configuration(CONFIG)
    policy = config.resolve_candidate_arbitration(AT.date())
    assert policy is not None
    engine = _live()
    engine.apply(_status("LO"))
    missing = engine.build(_snapshots())
    for envelope in _evidence():
        engine.apply(envelope)
    next_at = AT + timedelta(seconds=5)
    recovered = engine.build(_snapshots(next_at))
    contexts = {AT: missing, next_at: recovered}
    delivered: list[RealtimeSelection] = []

    def snapshots(_: ShadowArbitrationJournal, at: datetime) -> tuple[FeatureSnapshot, ...]:
        return _snapshots(at)

    def context(
        _: LiveDecisionContextEngine, features: tuple[FeatureSnapshot, ...]
    ) -> DecisionContext:
        return contexts[features[0].decision_at]

    monkeypatch.setattr(ShadowArbitrationJournal, "_snapshots", snapshots)
    monkeypatch.setattr(LiveDecisionContextEngine, "build", context)
    journal = ShadowArbitrationJournal(
        tmp_path / "shadow.arbitrations.jsonl",
        AT.date(),
        config.resolve(AT.date()),
        config.resolve_context(AT.date()),
        policy,
        regime_policy=_regime(),
        breadth_policy=_policy(),
        on_selection=delivered.append,
    )
    monkeypatch.setattr(journal, "_decision_times", (AT, next_at))
    try:
        journal.advance(next_at)
        assert not journal.failed and not delivered
        journal.advance(next_at + timedelta(seconds=5))
        assert not journal.failed and delivered
        assert all(item.candidate.decision_at == next_at for item in delivered)
    finally:
        journal.abort()


@pytest.mark.parametrize("status", ["HALT", "ATC", "SUSPEND"])
def test_disallowed_market_status_blocks_strategy_without_erasing_analysis(status: str) -> None:
    engine = _live()
    engine.apply(_status(status))
    for envelope in _evidence():
        engine.apply(envelope)
    context = engine.build(_snapshots())
    assert context.regime == "TREND_UP"
    assert not context.is_tradable
    assert all(
        item.block_reasons == ("market_status_ineligible",)
        for item in score_buy_first_baselines(_snapshots(), (context,))
    )


def test_reconnect_requires_new_market_authorization_but_keeps_analysis() -> None:
    engine = _live()
    engine.apply(_status("LO"))
    for envelope in _evidence():
        engine.apply(envelope)
    assert engine.build(_snapshots()).is_tradable
    envelope = _interval(
        21, "VIC", AT - timedelta(minutes=1), 10100, received_at=AT + timedelta(seconds=1)
    )
    engine.apply(envelope.model_copy(update={"stream_session_id": "segment-2"}))
    recovered = engine.build(_snapshots(AT + timedelta(seconds=5)))
    assert recovered.regime == "TREND_UP"
    assert not recovered.is_tradable
    assert recovered.market_statuses[0].reasons == ("MISSING_MARKET_STATUS",)
    engine.apply(_status("LO", AT + timedelta(seconds=6), "segment-2"))
    assert engine.build(_snapshots(AT + timedelta(seconds=10))).is_tradable


def test_missing_required_breadth_cannot_use_healthy_optional_sector_as_fallback() -> None:
    engine = _live()
    for envelope in _evidence():
        if envelope.symbol != "FPT":
            engine.apply(envelope)
    context = engine.build(_snapshots())
    assert context.breadth[1].upward_confirmation is True
    assert context.regime == "UNKNOWN"
    assert context.reasons == ("VN30_insufficient_participation",)


def test_optional_sector_failure_does_not_block_required_breadth() -> None:
    engine = _engine()
    for envelope in _evidence():
        engine.apply(envelope)
    broad, sector = engine.build(AT)
    sector = sector.model_copy(
        update={"reasons": ("membership_unavailable",), "upward_confirmation": None}
    )
    configuration = load_configuration(CONFIG)
    _, regime, reasons = market_state(
        (),
        (),
        configuration.resolve_context(AT.date()),
        regime_policy=_regime(),
        breadth=(broad, sector),
        breadth_policy=_policy(),
    )
    assert (regime, reasons) == ("TREND_UP", ())


@pytest.mark.parametrize(
    "mean,dispersion,confirmed,advance,expected",
    [
        ("100", "151", True, "0.8", "HIGH_DISPERSION"),
        ("-20", "10", False, "0.3", "TREND_DOWN"),
        ("-20", "10", False, "0.8", "RANGE"),
        ("5", "10", False, "0.8", "RANGE"),
        ("30", "10", False, "0.4", "RANGE"),
    ],
)
def test_regime_metrics_use_explicit_thresholds_not_official_index_volatility(
    mean: str, dispersion: str, confirmed: bool, advance: str, expected: str
) -> None:
    engine = _engine()
    for envelope in _evidence():
        engine.apply(envelope)
    broad = engine.build(AT)[0].model_copy(
        update={
            "mean_return_bps": Decimal(mean),
            "dispersion_bps": Decimal(dispersion),
            "upward_confirmation": confirmed,
            "advance_ratio": Decimal(advance),
        }
    )
    configuration = load_configuration(CONFIG)
    _, regime, reasons = market_state(
        (),
        (),
        configuration.resolve_context(AT.date()),
        regime_policy=_regime(),
        breadth=(broad,),
        breadth_policy=_policy(),
    )
    assert regime == expected and not reasons


def test_replay_matches_live_and_stale_or_future_receipts_do_not_provide_context() -> None:
    configuration = load_configuration(CONFIG)
    engine = _live()
    for envelope in _evidence():
        engine.apply(envelope)
    live = engine.build(_snapshots())
    future = _interval(
        21, "VIC", AT - timedelta(minutes=1), 9000, received_at=AT + timedelta(seconds=1)
    )
    replayed = build_decision_contexts(
        _snapshots(),
        (*_evidence(), future),
        configuration.resolve(AT.date()),
        configuration.resolve_context(AT.date()),
        regime_policy=_regime(),
        breadth_policy=_policy(),
        breadth_membership=_membership(),
    )[0]
    assert replayed.canonical_bytes() == live.canonical_bytes()
    stale = engine.build(_snapshots(AT + timedelta(seconds=91)))
    assert stale.regime == "UNKNOWN" and stale.market_confirmation_strength is None


def test_regime_reference_requires_captured_breadth_and_policy_identity_changes() -> None:
    configuration = load_configuration(CONFIG)
    payload = configuration.model_dump()
    payload["regimes"] = [_regime(reference_index="VNINDEX").model_dump()]
    with pytest.raises(ValueError, match="effective captured reference"):
        TradingConfiguration.model_validate(payload)
    with pytest.raises(ValueError, match="declared breadth policy"):
        context_identity(configuration.resolve_context(AT.date()), _regime(), None)
    original = context_identity(configuration.resolve_context(AT.date()), _regime(), _policy())
    changed = context_identity(
        configuration.resolve_context(AT.date()),
        _regime(),
        _policy(minimum_participation=Decimal("0.9")),
    )
    assert original != changed


def test_declared_regime_cannot_outlive_its_required_breadth_policy() -> None:
    configuration = load_configuration(CONFIG)
    payload = configuration.model_dump()
    payload["breadth"] = [
        configuration.breadth[0]
        .model_copy(update={"effective_to": date(2026, 10, 12)})
        .model_dump()
    ]
    with pytest.raises(ValueError, match="breadth version"):
        TradingConfiguration.model_validate(payload)


def test_emr_declared_basis_never_loads_historical_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object):
        raise AssertionError("historical REST must not substitute the declared realtime basis")

    monkeypatch.setattr("emr_jobs.t0_trading.research._historical_index_observations", forbidden)
    configuration = load_configuration(CONFIG)
    product = load_contracts(Path("lakehouse/contracts")).curated_product("market_data")
    contexts = _contexts(
        cast(Any, None),
        market_product=product,
        snapshots=_snapshots(),
        stream_envelopes=_evidence(),
        configuration=configuration.resolve(AT.date()),
        context_policy=configuration.resolve_context(AT.date()),
        regime_policy=_regime(),
        breadth_policy=_policy(),
        breadth_membership=_membership(),
    )
    assert contexts[0].regime == "TREND_UP" and contexts[0].data_mode == "LIVE"
    row = _context_rows(contexts, AT)[0]
    assert row["market_basis"] == "CONSTITUENT_BREADTH"
    assert row["regime_policy_sha256"] == _regime().sha256


def test_explicit_official_basis_separates_status_without_falling_back_to_breadth() -> None:
    configuration = load_configuration(CONFIG)
    policy = RegimeVersion(
        version="official-regime-v1",
        effective_from=AT.date(),
        effective_to=None,
        basis="OFFICIAL_INDEX",
    )
    assert market_state((), (), configuration.resolve_context(AT.date()), regime_policy=policy) == (
        None,
        "UNKNOWN",
        ("MISSING_REQUIRED_INDICES",),
    )
    with pytest.raises(ValueError, match="official regime uses"):
        RegimeVersion.model_validate({**policy.model_dump(), "reference_index": "VN30"})


def test_health_uses_declared_required_basis_and_keeps_optional_diagnostics() -> None:
    configuration = load_configuration(CONFIG).model_copy(update={"regimes": (_regime(),)})
    engine = _live()
    engine.apply(_status("LO"))
    for envelope in _evidence():
        engine.apply(envelope)
    context = engine.build(_snapshots())
    store = Mock()
    store.read_json.return_value = None
    report = operational_status(
        store,
        configuration,
        trade_date=AT.date(),
        previous_session=AT.date() - timedelta(days=1),
        context=context,
        observed_at=AT,
    )
    assert report.live_freshness == "CURRENT"
    assert report.market_health[
        "VNINDEX"
    ]  # Unavailable, but not this declared regime's requirement.
    assert report.market_health["breadth:VN30"] == ()
    stale = operational_status(
        store,
        configuration,
        trade_date=AT.date(),
        previous_session=AT.date() - timedelta(days=1),
        context=context,
        observed_at=AT + timedelta(seconds=6),
    )
    assert stale.live_freshness == "STALE_OR_INCOMPLETE"


def test_known_breadth_context_cannot_omit_required_evidence_or_claim_historical_proxy() -> None:
    engine = _live()
    for envelope in _evidence():
        engine.apply(envelope)
    context = engine.build(_snapshots())
    with pytest.raises(ValueError, match="eligible reference evidence"):
        DecisionContext.model_validate({**context.model_dump(), "breadth": ()})
    with pytest.raises(ValueError, match="historical proxy"):
        DecisionContext.model_validate({**context.model_dump(), "data_mode": "HISTORICAL_PROXY"})
