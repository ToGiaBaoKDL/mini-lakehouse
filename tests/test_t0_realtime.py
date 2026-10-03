"""Realtime is a bounded handoff of existing decisions, never a second strategy engine."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from t0_trading.arbitration.engine import CandidateArbitrator
from t0_trading.arbitration.journal import ShadowArbitrationJournal
from t0_trading.arbitration.model import CandidateArbitration
from t0_trading.arbitration.selection import RealtimeSelection
from t0_trading.configuration import load_configuration
from t0_trading.context import DecisionContext, LiveDecisionContextEngine
from t0_trading.features import FeatureSnapshot
from t0_trading.market.session import MarketSession
from t0_trading.realtime import RealtimeWorker
from t0_trading.strategy.baselines import BaselineCandidate, GroupEvidence

AT = datetime(2026, 10, 1, 2, 30, tzinfo=UTC)


def _selection(symbol: str = "VIC", at: datetime = AT) -> RealtimeSelection:
    feature = FeatureSnapshot(
        feature_version="microstructure-v1",
        configuration_version="market-state-v1",
        configuration_sha256="a" * 64,
        symbol=symbol,
        trade_date=at.date(),
        decision_at=at,
        market_session=MarketSession.CONTINUOUS_AM,
        stream_session_id="session-1",
        last_receive_sequence=10,
        trade_age_seconds=Decimal(1),
        quote_age_seconds=Decimal(1),
        mid_price=Decimal(100),
        microprice=Decimal(100),
        microprice_deviation_bps=Decimal(0),
        spread=Decimal(1),
        spread_bps=Decimal(100),
        bid_depth=600,
        ask_depth=400,
        level_one_imbalance=Decimal("0.2"),
        depth_imbalance=Decimal("0.1"),
        windows=(),
        reasons=(),
    )
    context = DecisionContext(
        context_version="decision-context-v3",
        context_configuration_sha256="b" * 64,
        feature_configuration_sha256=feature.configuration_sha256,
        data_mode="LIVE",
        trade_date=at.date(),
        decision_at=at,
        zones=(),
        indices=(),
        market_statuses=(),
        market_confirmation_strength=Decimal("0.5"),
        regime="TREND_UP",
        reasons=(),
    )
    candidate = BaselineCandidate(
        strategy="momentum_pullback",
        symbol=symbol,
        trade_date=at.date(),
        decision_at=at,
        feature_snapshot_sha256=feature.sha256,
        context_snapshot_sha256=context.sha256,
        context_version=context.context_version,
        context_configuration_sha256=context.context_configuration_sha256,
        context_data_mode="LIVE",
        market_regime=context.regime,
        groups=(
            GroupEvidence(name="trend_strength", strength=Decimal("0.5")),
            GroupEvidence(name="pullback_quality", strength=Decimal("0.5")),
            GroupEvidence(name="reacceleration_confirmation", strength=Decimal("0.5")),
        ),
        strength=Decimal("0.5"),
        block_reasons=(),
    )
    arbitration = CandidateArbitration(
        arbitration_version="candidate-arbitration-v1",
        arbitration_configuration_sha256="c" * 64,
        candidate_version=candidate.baseline_version,
        candidate_sha256=candidate.sha256,
        strategy=candidate.strategy,
        symbol=symbol,
        trade_date=at.date(),
        decision_at=at,
        horizon_seconds=300,
        strength=candidate.strength,
        status="SELECTED",
        priority=0,
        selected_candidate_sha256=candidate.sha256,
    )
    return RealtimeSelection(
        candidate=candidate, arbitration=arbitration, context=context, features=(feature,)
    )


def _wait_until(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 2
    while not predicate() and monotonic() < deadline:
        sleep(0.005)
    assert predicate()


def test_realtime_delivers_exact_selection_once_and_discards_late_or_future_clocks() -> None:
    delivered: list[RealtimeSelection] = []
    worker = RealtimeWorker(delivered.append, maximum_delay=timedelta(seconds=15), clock=lambda: AT)
    try:
        worker.offer(_selection(at=AT - timedelta(seconds=16)))
        _wait_until(lambda: worker.health().stale == 1)
        selection = _selection()
        worker.offer(selection)
        worker.offer(selection)
        _wait_until(lambda: worker.health().duplicates == 1)
        worker.offer(_selection("VHM", AT + timedelta(seconds=1)))
        _wait_until(lambda: worker.health().stale == 2)
        assert delivered == [selection]
        assert worker.health().delivered == 1
    finally:
        worker.close()


def test_sink_error_disables_only_its_symbol_without_exposing_exception_text() -> None:
    delivered: list[str] = []

    def sink(selection: RealtimeSelection) -> None:
        if selection.candidate.symbol == "VIC":
            raise RuntimeError("secret connection details")
        delivered.append(selection.candidate.symbol)

    worker = RealtimeWorker(sink, maximum_delay=timedelta(seconds=15), clock=lambda: AT)
    try:
        worker.offer(_selection())
        worker.offer(_selection("VHM"))
        _wait_until(lambda: worker.health().delivered == 1)
        assert delivered == ["VHM"]
        assert worker.health().failed_symbols == ("VIC",)
        assert "secret" not in worker.health().model_dump_json()
    finally:
        worker.close()


def test_conflicting_selection_for_one_clock_is_not_treated_as_an_exact_retry() -> None:
    delivered: list[RealtimeSelection] = []
    worker = RealtimeWorker(delivered.append, maximum_delay=timedelta(seconds=15), clock=lambda: AT)
    selection = _selection()
    try:
        worker.offer(selection)
        _wait_until(lambda: worker.health().delivered == 1)
        conflict = RealtimeSelection(
            candidate=selection.candidate,
            arbitration=selection.arbitration.model_copy(update={"priority": 1}),
            context=selection.context,
            features=selection.features,
        )
        worker.offer(conflict)
        _wait_until(lambda: worker.health().failure == "CONFLICTING_SELECTION")
        assert worker.health().failed_symbols == ("VIC",)
        assert delivered == [selection]
    finally:
        worker.close()


def test_overflow_is_fail_closed_and_capture_producer_does_not_wait_for_sink() -> None:
    entered, release = Event(), Event()

    def sink(selection: RealtimeSelection) -> None:
        entered.set()
        assert release.wait(2)

    worker = RealtimeWorker(sink, maximum_delay=timedelta(seconds=15), capacity=1, clock=lambda: AT)
    try:
        worker.offer(_selection())
        assert entered.wait(2)
        worker.offer(_selection("VHM"))
        worker.offer(_selection("VHM"))
        assert worker.health().failure == "QUEUE_OVERFLOW"
        assert not worker.health().enabled
    finally:
        release.set()
        health = worker.close()
    assert health.queued == 0


def test_disconnect_invalidates_pending_selections_but_not_future_warmed_decisions() -> None:
    delivered: list[RealtimeSelection] = []
    worker = RealtimeWorker(delivered.append, maximum_delay=timedelta(seconds=15), clock=lambda: AT)
    try:
        worker.disconnected(AT - timedelta(seconds=1))
        worker.offer(_selection(at=AT - timedelta(seconds=2)))
        _wait_until(lambda: worker.health().disconnected == 1)
        worker.offer(_selection())
        _wait_until(lambda: worker.health().delivered == 1)
        assert delivered == [_selection()]
    finally:
        worker.close()


def test_shutdown_does_not_deliver_queued_decisions_or_wait_indefinitely() -> None:
    entered, release = Event(), Event()
    delivered: list[str] = []

    def sink(selection: RealtimeSelection) -> None:
        entered.set()
        assert release.wait(2)
        delivered.append(selection.candidate.symbol)

    worker = RealtimeWorker(sink, maximum_delay=timedelta(seconds=15), clock=lambda: AT)
    try:
        worker.offer(_selection())
        assert entered.wait(2)
        worker.offer(_selection("VHM"))
        health = worker.close(timeout=0)
        assert health.failure == "SHUTDOWN_TIMEOUT"
        assert health.queued == 0
        worker.offer(_selection("VHM"))
        assert worker.health().queued == 0
    finally:
        release.set()
        worker.close()
    assert delivered == ["VIC"]


@pytest.mark.parametrize("field", ["context", "features", "arbitration"])
def test_selection_rejects_mixed_lineage(field: str) -> None:
    first, other = _selection(), _selection(at=AT + timedelta(seconds=5))
    with pytest.raises(ValueError):
        RealtimeSelection(**{**first.model_dump(), field: getattr(other, field)})


@pytest.mark.parametrize("consumer_fails", [False, True])
@pytest.mark.parametrize("live", [False, True])
def test_journal_handoff_preserves_bytes_and_consumer_failure_cannot_fail_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer_fails: bool, live: bool
) -> None:
    selection = _selection()
    config = load_configuration(Path("t0-trading/config/trading.yaml"))
    policy = config.resolve_candidate_arbitration(AT.date())
    assert policy is not None
    received: list[RealtimeSelection] = []

    def sink(value: RealtimeSelection) -> None:
        received.append(value)
        if consumer_fails:
            raise RuntimeError("consumer failure")

    journals = [
        ShadowArbitrationJournal(
            tmp_path / f"{name}.arbitrations.jsonl",
            AT.date(),
            config.resolve(AT.date()),
            config.resolve_context(AT.date()),
            policy,
            on_selection=callback,
        )
        for name, callback in (("reference", None), ("realtime", sink))
    ]

    def score(*_: object, history: object = ()) -> tuple[BaselineCandidate, ...]:
        return (selection.candidate,)

    def snapshots(_: ShadowArbitrationJournal, at: datetime) -> tuple[FeatureSnapshot, ...]:
        assert at == AT
        return selection.features

    def context(_: LiveDecisionContextEngine, features: object) -> DecisionContext:
        assert features == selection.features
        return selection.context

    def decide(_: CandidateArbitrator, candidates: object) -> tuple[CandidateArbitration, ...]:
        assert candidates == (selection.candidate,)
        return (selection.arbitration,)

    monkeypatch.setattr("t0_trading.arbitration.journal.score_buy_first_baselines", score)
    monkeypatch.setattr(ShadowArbitrationJournal, "_snapshots", snapshots)
    monkeypatch.setattr(LiveDecisionContextEngine, "build", context)
    monkeypatch.setattr(CandidateArbitrator, "decide", decide)
    try:
        for journal in journals:
            monkeypatch.setattr(journal, "_decision_times", (AT,))
            if live:
                journal.advance(
                    AT + timedelta(seconds=config.resolve(AT.date()).features.cadence_seconds)
                )
            else:
                journal.disconnected(AT, unavailable=True)
            assert not journal.failed
        assert received == ([selection] if live else [])
        assert journals[0].partial_output.read_bytes() == journals[1].partial_output.read_bytes()
        assert (
            journals[0].partial_candidate_output.read_bytes()
            == journals[1].partial_candidate_output.read_bytes()
        )
    finally:
        for journal in journals:
            journal.abort()
