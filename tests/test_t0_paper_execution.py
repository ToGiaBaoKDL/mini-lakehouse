"""Paper execution is causal, fail-closed, and broker neutral."""

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from t0_trading.arbitration import CandidateArbitration
from t0_trading.configuration import load_configuration
from t0_trading.controls import (
    AccountPosition,
    AccountSnapshot,
    RiskLimits,
    SelectionEvidence,
    public_vndirect_dta_costs,
)
from t0_trading.execution import (
    PaperExecutionRequest,
    PaperExitRequest,
    PaperFillEvidence,
    PaperMarketSnapshot,
    PaperOrderEvent,
    initialize_paper_ledger,
    plan_paper_exit,
    plan_paper_order,
    reduce_paper_order,
    reserve_paper_entry,
    settle_paper_entry,
)
from t0_trading.execution.postgres import PaperConflict, PostgresPaperRepository
from t0_trading.execution.readiness import PaperReadiness
from t0_trading.execution.runtime import (
    PaperRuntime,
    PaperSession,
    advance_clock,
    cancel_order,
    process_quote,
    submit_entry,
    submit_exit,
)
from t0_trading.persistence import PostgresConnection
from t0_trading.promotion import PromotionGateReport, PromotionTargetResult
from t0_trading.promotion.evidence import resolve_research_lineage
from t0_trading.strategy.baselines import (
    BASELINE_GROUP_NAMES,
    BaselineCandidate,
    BaselineName,
    GroupEvidence,
)

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = date(2026, 10, 26)
DECISION_AT = datetime(2026, 10, 26, 2, 30, tzinfo=UTC)


def _promotion_report() -> PromotionGateReport:
    configuration = load_configuration(CONFIGURATION)
    evaluation = configuration.resolve_baseline_evaluation(TRADE_DATE, "PROMOTION")
    gate = configuration.resolve_promotion_gate(TRADE_DATE)
    arbitration = configuration.resolve_candidate_arbitration(TRADE_DATE)
    assert gate is not None and arbitration is not None
    return PromotionGateReport(
        gate_version=gate.version,
        gate_configuration_sha256=gate.sha256,
        evaluation_version=evaluation.version,
        evaluation_configuration_sha256=evaluation.sha256,
        arbitration_version=arbitration.version,
        arbitration_configuration_sha256=arbitration.sha256,
        required_session_count=26,
        observed_session_count=26,
        holdout_dates=tuple(date(2026, 10, day) for day in range(19, 24)),
        capture_gap_count=0,
        walk_forward_sha256="4" * 64,
        shadow_audit_sha256s=tuple(str(index) * 64 for index in range(5, 10)),
        status="PASS",
        reasons=(),
        targets=tuple(
            PromotionTargetResult(
                strategy=cast(BaselineName, target.strategy),
                symbol=target.symbol,
                horizon_seconds=target.horizon_seconds,
                status="PASS",
                reasons=(),
                selected_count=10,
                eligible_outcome_count=10,
                positive_net_count=8,
                outcome_coverage_rate=Decimal(1),
                positive_net_rate=Decimal("0.8"),
                average_net_return_bps=Decimal(5),
                worst_fold_net_return_bps=Decimal(2),
            )
            for target in gate.targets
        ),
    )


def _request(
    *,
    market_status: str = "LO",
    observed_delay: int = 1,
    quote_age: int = 0,
    ask_quantity: int = 100,
    cash: str = "20000",
    settled: int = 200,
) -> PaperExecutionRequest:
    candidate = BaselineCandidate(
        strategy="momentum_pullback",
        symbol="VIC",
        trade_date=TRADE_DATE,
        decision_at=DECISION_AT,
        feature_snapshot_sha256="1" * 64,
        groups=cast(
            tuple[GroupEvidence, GroupEvidence, GroupEvidence],
            tuple(
                GroupEvidence(name=name, strength=Decimal("0.8"))
                for name in BASELINE_GROUP_NAMES["momentum_pullback"]
            ),
        ),
        strength=Decimal("0.8"),
        block_reasons=(),
    )
    configuration = load_configuration(CONFIGURATION)
    arbitration_policy = configuration.resolve_candidate_arbitration(TRADE_DATE)
    assert arbitration_policy is not None
    arbitration = CandidateArbitration(
        arbitration_version=arbitration_policy.version,
        arbitration_configuration_sha256=arbitration_policy.sha256,
        candidate_version=candidate.baseline_version,
        candidate_sha256=candidate.sha256,
        strategy=candidate.strategy,
        symbol=candidate.symbol,
        trade_date=candidate.trade_date,
        decision_at=candidate.decision_at,
        horizon_seconds=300,
        strength=candidate.strength,
        status="SELECTED",
        priority=0,
        selected_candidate_sha256=candidate.sha256,
    )
    observed_at = DECISION_AT + timedelta(seconds=observed_delay)
    account = AccountSnapshot(
        as_of=DECISION_AT - timedelta(minutes=1),
        source="paper account snapshot",
        cash_vnd=Decimal(cash),
        positions=(
            AccountPosition(
                symbol="VIC",
                settled_qty=settled,
                t1_qty=0,
                t2_qty=0,
                core_min_qty=min(100, settled),
                start_price=Decimal(90),
            ),
        ),
    )
    return PaperExecutionRequest(
        candidate=candidate,
        arbitration=arbitration,
        promotion=_promotion_report(),
        selection=SelectionEvidence(
            arbitration_sha256=arbitration.sha256,
            selected_at=DECISION_AT + timedelta(milliseconds=100),
            source="committed shadow arbitration journal",
            source_sha256="2" * 64,
        ),
        market=PaperMarketSnapshot(
            symbol="VIC",
            observed_at=observed_at,
            quote_received_at=observed_at - timedelta(seconds=quote_age),
            stream_session_id="session-1",
            receive_sequence=10,
            best_ask_price=Decimal(100),
            best_ask_quantity=ask_quantity,
            best_bid_price=Decimal(99),
            best_bid_quantity=100,
            reference_price=Decimal(100),
            ceiling_price=Decimal(200),
            floor_price=Decimal(50),
            tick_size=Decimal(1),
            market_status=market_status,
            source_sha256="3" * 64,
        ),
        account=account,
        costs=public_vndirect_dta_costs(
            TRADE_DATE,
            checked_at=datetime(2026, 9, 22, tzinfo=UTC),
        ),
        risk=RiskLimits(
            max_cycle_quantity=100,
            max_order_notional_vnd=Decimal(20000),
            max_cycles_per_day=2,
            max_daily_loss_vnd=Decimal(1000),
            cash_reserve_vnd=Decimal(0),
        ),
        resources=initialize_paper_ledger(account, TRADE_DATE),
    )


def _policy():
    configuration = load_configuration(CONFIGURATION)
    policy = configuration.resolve_paper_execution(TRADE_DATE)
    arbitration = configuration.resolve_candidate_arbitration(TRADE_DATE)
    context = configuration.resolve_context(TRADE_DATE)
    promotion = configuration.resolve_promotion_gate(TRADE_DATE)
    assert policy is not None and arbitration is not None and promotion is not None
    return policy, arbitration, context, promotion


def test_paper_planner_emits_a_deterministic_paper_only_intent() -> None:
    request = _request()

    policy, arbitration, context, promotion = _policy()
    plan = plan_paper_order(request, policy, arbitration, context, promotion)

    assert plan.status == "READY"
    assert plan.intent is not None
    assert plan.intent.mode == "PAPER"
    assert plan.intent.leg == "ENTRY"
    assert plan.intent.action == "BUY"
    assert plan.intent.quantity == 100
    assert plan.intent.limit_price == Decimal(100)
    assert plan.intent.reserved_cash_vnd == Decimal(10010)
    assert plan.intent.reserved_exit_quantity == 100
    assert plan == plan_paper_order(request, policy, arbitration, context, promotion)
    assert "outcome" not in plan.model_dump_json().lower()


def _runtime_case(quantity: int = 100):
    config = load_configuration(CONFIGURATION)
    policy = config.resolve_paper_execution(TRADE_DATE)
    assert policy is not None
    config = config.model_copy(
        update={"paper_executions": (policy.model_copy(update={"order_quantity": quantity}),)}
    )
    request = _request(cash="50000", settled=400, ask_quantity=quantity)
    request = request.model_copy(
        update={"risk": request.risk.model_copy(update={"max_cycle_quantity": quantity})}
    )
    lineage = resolve_research_lineage(config, TRADE_DATE, request.costs)
    candidate = request.candidate.model_copy(
        update={
            "context_snapshot_sha256": "b" * 64,
            "context_configuration_sha256": lineage.context_configuration_sha256,
            "context_version": config.resolve_context(TRADE_DATE).version,
            "context_data_mode": "LIVE",
            "market_regime": "TREND_UP",
        }
    )
    arbitration = request.arbitration.model_copy(
        update={
            "candidate_sha256": candidate.sha256,
            "selected_candidate_sha256": candidate.sha256,
        }
    )
    request = request.model_copy(
        update={
            "candidate": candidate,
            "arbitration": arbitration,
            "selection": request.selection.model_copy(
                update={"arbitration_sha256": arbitration.sha256}
            ),
        }
    )
    session = PaperSession(account=request.account, costs=request.costs, ledger=request.resources)
    ready = PaperReadiness(
        trade_date=TRADE_DATE,
        previous_session=date(2026, 10, 23),
        mode="PAPER",
        reason="PROMOTION_PASS",
        promotion=request.promotion,
        research_lineage=lineage,
    )
    return config, request, session, ready


def _next_quote(request: PaperExecutionRequest, offset: int = 1, quantity: int = 100):
    at = request.market.observed_at + timedelta(seconds=offset)
    return request.market.model_copy(
        update={
            "observed_at": at,
            "quote_received_at": at,
            "receive_sequence": request.market.receive_sequence + offset,
            "best_ask_quantity": quantity,
            "source_sha256": f"{offset:064x}",
        }
    )


@pytest.mark.parametrize("changed", ["features", "outcomes", "costs", "candidate_context"])
def test_paper_admission_rechecks_readiness_against_active_session_assumptions(
    changed: str,
) -> None:
    config, request, session, ready = _runtime_case()
    if changed == "features":
        version = config.resolve(TRADE_DATE)
        config = config.model_copy(
            update={
                "versions": (
                    version.model_copy(
                        update={
                            "features": version.features.model_copy(
                                update={"warmup_seconds": version.features.warmup_seconds + 60}
                            )
                        }
                    ),
                )
            }
        )
    elif changed == "outcomes":
        outcome = config.resolve_outcomes(TRADE_DATE)
        config = config.model_copy(
            update={
                "outcomes": (
                    outcome.model_copy(
                        update={
                            "execution_latency_milliseconds": outcome.execution_latency_milliseconds
                            + 1
                        }
                    ),
                )
            }
        )
    elif changed == "costs":
        costs = session.costs.model_copy(update={"buy_fee_bps": session.costs.buy_fee_bps + 1})
        session = session.model_copy(update={"costs": costs})
        request = request.model_copy(update={"costs": costs})
    else:
        request = request.model_copy(
            update={
                "candidate": request.candidate.model_copy(
                    update={"context_configuration_sha256": "a" * 64}
                )
            }
        )
    with pytest.raises(ValueError, match="admission lineage mismatch"):
        submit_entry(session, request, ready, config)
    assert session.revision == 0 and not session.intents


def test_runtime_replays_partial_fills_and_settles_only_once() -> None:
    config, request, initial, ready = _runtime_case(200)
    submitted = submit_entry(initial, request, ready, config)
    assert len(submitted.intents) == 1
    intent = submitted.intents[0]
    assert submit_entry(submitted, request, ready, config) == submitted
    partial = process_quote(submitted, _next_quote(request), config)
    assert reduce_paper_order(intent, partial.events(intent)).status == "PARTIALLY_FILLED"
    assert partial.ledger.reservations[0].status == "ENTRY_PENDING"
    # Rehydrate the exact durable checkpoint: no ephemeral matcher state is required.
    restarted = PaperSession.model_validate_json(partial.model_dump_json())
    assert process_quote(restarted, _next_quote(request), config) == restarted
    filled = process_quote(restarted, _next_quote(request, 2, 200), config)
    assert reduce_paper_order(intent, filled.events(intent)).status == "FILLED"
    assert filled.ledger.reservations[0].reserved_exit_quantity == 200
    assert filled.ledger.available_cash_vnd == Decimal(29980)
    assert advance_clock(filled, intent.expires_at) == filled
    assert PaperSession.model_validate(filled.model_dump()) == filled


def test_runtime_never_fills_from_planning_quote_or_unchanged_book() -> None:
    config, request, initial, ready = _runtime_case()
    submitted = submit_entry(initial, request, ready, config)
    planning = process_quote(submitted, request.market, config)
    unchanged = process_quote(planning, _next_quote(request), config)
    assert (
        reduce_paper_order(submitted.intents[0], unchanged.events(submitted.intents[0])).status
        == "ACCEPTED"
    )
    expired = advance_clock(unchanged, submitted.intents[0].expires_at)
    assert expired.ledger.available_cash_vnd == initial.ledger.available_cash_vnd
    assert expired.ledger.positions == initial.ledger.positions
    assert not expired.ledger.reservations


def test_runtime_partial_expiry_releases_only_unused_resources() -> None:
    config, request, initial, ready = _runtime_case(200)
    submitted = submit_entry(initial, request, ready, config)
    intent = submitted.intents[0]
    partial = process_quote(submitted, _next_quote(request), config)
    expired = advance_clock(partial, intent.expires_at)
    assert expired.ledger.available_cash_vnd == Decimal(39990)
    assert expired.ledger.reservations[0].reserved_exit_quantity == 100
    assert expired.ledger.positions[0].available_exit_quantity == 200
    assert advance_clock(expired, intent.expires_at + timedelta(seconds=1)) == expired


def test_runtime_exit_does_not_credit_unsettled_sale_proceeds() -> None:
    config, request, initial, ready = _runtime_case()
    submitted = submit_entry(initial, request, ready, config)
    entry = submitted.intents[0]
    filled = process_quote(submitted, _next_quote(request), config)
    market = _next_quote(request, 300).model_copy(
        update={"best_bid_price": Decimal(110), "best_ask_price": Decimal(111)}
    )
    exiting = submit_exit(filled, entry, market, config)
    assert len(exiting.intents) == 2
    assert submit_exit(exiting, entry, market, config) == exiting
    later = market.model_copy(
        update={
            "observed_at": market.observed_at + timedelta(seconds=1),
            "quote_received_at": market.observed_at + timedelta(seconds=1),
            "receive_sequence": 999,
        }
    )
    closed = process_quote(exiting, later, config)
    assert not closed.ledger.reservations
    assert closed.ledger.realized_net_pnl_vnd > 0
    assert closed.ledger.available_cash_vnd == filled.ledger.available_cash_vnd


def test_runtime_status_cancel_and_cursor_conflict_are_fail_closed() -> None:
    config, request, initial, ready = _runtime_case()
    submitted = submit_entry(initial, request, ready, config)
    halted = _next_quote(request).model_copy(update={"market_status": "HALT"})
    cancelled = process_quote(submitted, halted, config)
    intent = submitted.intents[0]
    assert reduce_paper_order(intent, cancelled.events(intent)).status == "CANCELLED"
    assert cancelled.ledger.available_cash_vnd == initial.ledger.available_cash_vnd
    assert cancel_order(cancelled, intent, halted.observed_at, reason="STOP") == cancelled
    with pytest.raises(ValueError, match="cursor"):
        process_quote(cancelled, halted.model_copy(update={"best_ask_quantity": 200}), config)


def test_runtime_quote_at_expiry_expires_instead_of_filling() -> None:
    config, request, initial, ready = _runtime_case()
    submitted = submit_entry(initial, request, ready, config)
    result = process_quote(submitted, _next_quote(request, 5), config)
    intent = submitted.intents[0]
    assert reduce_paper_order(intent, result.events(intent)).status == "EXPIRED"


def test_paper_checkpoint_rejects_cash_drift_and_removed_history() -> None:
    config, request, initial, ready = _runtime_case()
    submitted = submit_entry(initial, request, ready, config)
    for updates in ({"operations": ()}, {"ledger": initial.ledger}):
        with pytest.raises(ValueError, match="reconcile"):
            PaperSession.model_validate({**submitted.model_dump(), **updates})


def test_paper_consumer_disabled_and_repository_failure_do_not_escape() -> None:
    config, request, initial, ready = _runtime_case()

    class Repository:
        def load(self) -> PaperSession:
            return initial

        def commit(self, previous: PaperSession, current: PaperSession) -> None:
            raise RuntimeError("sensitive connection details must not be emitted")

    repository = Repository()
    assert PaperRuntime(repository, config).entry(request, ready).status == "OBSERVE_ONLY"
    result = PaperRuntime(repository, config, enabled=True).entry(request, ready)
    assert result.status == "ERROR"
    assert result.reason == "RuntimeError"
    assert result.state_sha256 is None


def test_paper_consumer_distinguishes_blocks_retries_and_commits() -> None:
    config, request, initial, ready = _runtime_case()

    class Repository:
        state = initial
        writes = 0

        def load(self) -> PaperSession:
            return self.state

        def commit(self, previous: PaperSession, current: PaperSession) -> None:
            assert previous == self.state
            self.state = current
            self.writes += 1

    repository = Repository()
    runtime = PaperRuntime(repository, config, enabled=True)
    blocked = request.model_copy(
        update={"market": request.market.model_copy(update={"market_status": "HALT"})}
    )
    result = runtime.entry(blocked, ready)
    assert result.status == "BLOCKED"
    assert "MARKET_STATUS_INELIGIBLE" in result.block_reasons
    assert repository.writes == 0
    assert runtime.entry(request, ready).status == "COMMITTED"
    assert runtime.entry(request, ready).status == "NO_CHANGE"
    assert repository.writes == 1


def test_paper_quote_rejects_a_different_session_date() -> None:
    config, request, initial, _ = _runtime_case()
    quote = _next_quote(request)
    next_day = quote.model_copy(
        update={
            "observed_at": quote.observed_at + timedelta(days=1),
            "quote_received_at": quote.quote_received_at + timedelta(days=1),
        }
    )
    with pytest.raises(ValueError, match="session date"):
        process_quote(initial, next_day, config)


def test_paper_multiple_orders_cannot_reuse_one_quotes_liquidity() -> None:
    config, request, initial, ready = _runtime_case()
    first = submit_entry(initial, request, ready, config)
    candidate = request.candidate.model_copy(
        update={"decision_at": DECISION_AT + timedelta(seconds=1)}
    )
    arbitration = request.arbitration.model_copy(
        update={
            "decision_at": candidate.decision_at,
            "candidate_sha256": candidate.sha256,
            "selected_candidate_sha256": candidate.sha256,
        }
    )
    second_request = PaperExecutionRequest.model_validate(
        {
            **request.model_dump(),
            "candidate": candidate,
            "arbitration": arbitration,
            "resources": first.ledger,
            "selection": request.selection.model_copy(
                update={
                    "arbitration_sha256": arbitration.sha256,
                    "selected_at": candidate.decision_at,
                }
            ),
        }
    )
    second = submit_entry(first, second_request, ready, config)
    assert len(second.intents) == 2
    matched = process_quote(second, _next_quote(request), config)
    assert (
        sum(
            reduce_paper_order(item, matched.events(item)).cumulative_filled_quantity
            for item in matched.intents
        )
        == 100
    )


@pytest.mark.integration
def test_postgres_paper_atomic_commit_retry_conflict_and_rollback() -> None:
    """Run only against an explicitly supplied disposable PostgreSQL instance."""
    dsn = os.environ.get("T0_PAPER_TEST_DSN")
    if not dsn:
        pytest.skip("T0_PAPER_TEST_DSN is required for isolated PostgreSQL verification")
    psycopg = pytest.importorskip("psycopg")
    config, request, initial, ready = _runtime_case()
    next_state = submit_entry(initial, request, ready, config)
    namespace = "paper_test_" + uuid4().hex
    schema = (
        Path("infra/runtime/postgres/bootstrap/t0_trading.sql")
        .read_text()
        .split("SET ROLE t0_trading;", 1)[1]
        .split("RESET ROLE;", 1)[0]
    )
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{namespace}"')
        try:
            connection.execute(f'SET search_path TO "{namespace}"')
            connection.execute(schema)
            repo = PostgresPaperRepository(
                cast(PostgresConnection, connection),
                trade_date=TRADE_DATE,
                account_sha256=initial.account.sha256,
            )
            repo.initialize(initial)
            assert repo.load() == initial
            repo.commit(initial, next_state)
            repo.commit(initial, next_state)  # Lost acknowledgement, exact retry.
            assert repo.load() == next_state
            assert connection.execute("SELECT count(*) FROM paper_operations").fetchone()[0] == 2
            conflicting = process_quote(initial, _next_quote(request), config)
            with pytest.raises(PaperConflict):
                repo.commit(initial, conflicting)
            expired = advance_clock(next_state, next_state.intents[0].expires_at)
            connection.execute(
                "ALTER TABLE paper_operations ADD CONSTRAINT test_fail CHECK(sequence < 3)"
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                repo.commit(next_state, expired)
            assert repo.load() == next_state  # Checkpoint UPDATE was rolled back with the INSERT.
            connection.execute("ALTER TABLE paper_operations DROP CONSTRAINT test_fail")
            repo.commit(next_state, expired)
            assert repo.load() == expired
            # A new snapshot cannot silently reset the same account's daily resource budget.
            with pytest.raises(psycopg.errors.UniqueViolation):
                connection.execute(
                    "INSERT INTO paper_sessions SELECT trade_date, %s, revision, state_sha256, "
                    "state_json FROM paper_sessions",
                    ("f" * 64,),
                )
            connection.execute(
                "UPDATE paper_operations SET operation_sha256 = %s WHERE sequence = 1", ("e" * 64,)
            )
            with pytest.raises(ValueError, match="operation log disagree"):
                repo.load()
        finally:
            connection.execute(f'DROP SCHEMA "{namespace}" CASCADE')


def test_paper_planner_records_operational_blocks_without_an_order_intent() -> None:
    request = _request(
        market_status="BREAK",
        quote_age=6,
        ask_quantity=50,
        cash="10000",
        settled=100,
    )

    plan = plan_paper_order(request, *_policy())

    assert plan.status == "BLOCKED"
    assert plan.intent is None
    assert plan.reasons == (
        "MARKET_STATUS_INELIGIBLE",
        "STALE_QUOTE",
        "INSUFFICIENT_ASK_QUANTITY",
        "INSUFFICIENT_SETTLED_ABOVE_CORE",
        "CASH_LIMIT",
    )


def test_resource_ledger_rejects_stale_competing_reservation_and_releases_cancel() -> None:
    request = _request()
    first = plan_paper_order(request, *_policy()).intent
    assert first is not None
    later_market = request.market.model_copy(
        update={
            "observed_at": request.market.observed_at + timedelta(seconds=1),
            "quote_received_at": request.market.quote_received_at + timedelta(seconds=1),
            "receive_sequence": request.market.receive_sequence + 1,
            "source_sha256": "f" * 64,
        }
    )
    competing = plan_paper_order(
        request.model_copy(update={"market": later_market}), *_policy()
    ).intent
    assert competing is not None and competing.sha256 != first.sha256

    reserved = reserve_paper_entry(request.resources, first)

    assert reserved.revision == 1
    assert reserved.available_cash_vnd == Decimal(9990)
    assert reserved.positions[0].available_exit_quantity == 0
    with pytest.raises(ValueError, match="ledger revision"):
        reserve_paper_entry(reserved, competing)

    accepted = PaperOrderEvent(
        intent_sha256=first.sha256,
        sequence=1,
        event_type="ACCEPTED",
        occurred_at=first.created_at + timedelta(milliseconds=10),
        cumulative_filled_quantity=0,
        adapter_event_id="accepted-for-cancel",
        adapter_evidence_sha256="a" * 64,
    )
    cancelled = PaperOrderEvent(
        intent_sha256=first.sha256,
        sequence=2,
        event_type="CANCELLED",
        occurred_at=first.created_at + timedelta(milliseconds=20),
        cumulative_filled_quantity=0,
        reason="NO_FILL",
        adapter_event_id="cancelled-no-fill",
        adapter_evidence_sha256="b" * 64,
    )
    released = settle_paper_entry(
        reserved,
        first,
        reduce_paper_order(first, (accepted, cancelled)),
        request.costs,
    )

    assert released.revision == 2
    assert released.available_cash_vnd == request.account.cash_vnd
    assert released.positions[0].available_exit_quantity == 100
    assert released.reservations == ()


def test_paper_request_requires_a_prior_matching_promotion_pass() -> None:
    request = _request()
    pending = request.promotion.model_copy(
        update={
            "status": "PENDING",
            "targets": tuple(
                item.model_copy(
                    update={"status": "PENDING", "reasons": ("INSUFFICIENT_SELECTIONS",)}
                )
                for item in request.promotion.targets
            ),
        }
    )
    payload = request.model_dump(mode="python")
    payload["promotion"] = pending.model_dump(mode="python")

    with pytest.raises(ValidationError, match="lacks prior matching promotion evidence"):
        PaperExecutionRequest.model_validate(payload)


def test_paper_order_lifecycle_accepts_partial_then_complete_fill() -> None:
    intent = plan_paper_order(_request(), *_policy()).intent
    assert intent is not None
    accepted = PaperOrderEvent(
        intent_sha256=intent.sha256,
        sequence=1,
        event_type="ACCEPTED",
        occurred_at=intent.created_at + timedelta(milliseconds=10),
        cumulative_filled_quantity=0,
        adapter_event_id="accepted-1",
        adapter_evidence_sha256="a" * 64,
    )
    partial = PaperOrderEvent(
        intent_sha256=intent.sha256,
        sequence=2,
        event_type="PARTIALLY_FILLED",
        occurred_at=intent.created_at + timedelta(milliseconds=20),
        cumulative_filled_quantity=40,
        average_fill_price=Decimal(100),
        adapter_event_id="fill-1",
        adapter_evidence_sha256="b" * 64,
        fill_evidence=PaperFillEvidence(
            symbol="VIC",
            action="BUY",
            observed_at=intent.created_at + timedelta(milliseconds=15),
            stream_session_id="session-1",
            receive_sequence=11,
            filled_quantity=40,
            available_quantity=100,
            fill_price=Decimal(100),
            source_sha256="c" * 64,
        ),
    )
    filled = PaperOrderEvent(
        intent_sha256=intent.sha256,
        sequence=3,
        event_type="FILLED",
        occurred_at=intent.created_at + timedelta(milliseconds=30),
        cumulative_filled_quantity=100,
        average_fill_price=Decimal("99.4"),
        adapter_event_id="fill-2",
        adapter_evidence_sha256="d" * 64,
        fill_evidence=PaperFillEvidence(
            symbol="VIC",
            action="BUY",
            observed_at=intent.created_at + timedelta(milliseconds=25),
            stream_session_id="session-1",
            receive_sequence=12,
            filled_quantity=60,
            available_quantity=60,
            fill_price=Decimal(99),
            source_sha256="e" * 64,
        ),
    )

    state = reduce_paper_order(intent, (accepted, partial, filled))

    assert state.status == "FILLED"
    assert state.cumulative_filled_quantity == 100
    assert state.event_count == 3
    assert state.last_event_sha256 == filled.sha256


def test_paper_order_lifecycle_rejects_impossible_or_price_violating_history() -> None:
    intent = plan_paper_order(_request(), *_policy()).intent
    assert intent is not None
    filled_without_acceptance = PaperOrderEvent(
        intent_sha256=intent.sha256,
        sequence=1,
        event_type="FILLED",
        occurred_at=intent.created_at + timedelta(milliseconds=10),
        cumulative_filled_quantity=100,
        average_fill_price=Decimal(100),
        adapter_event_id="fill-without-accept",
        adapter_evidence_sha256="6" * 64,
        fill_evidence=PaperFillEvidence(
            symbol="VIC",
            action="BUY",
            observed_at=intent.created_at + timedelta(milliseconds=5),
            stream_session_id="session-1",
            receive_sequence=11,
            filled_quantity=100,
            available_quantity=100,
            fill_price=Decimal(100),
            source_sha256="7" * 64,
        ),
    )
    with pytest.raises(ValueError, match="history is inconsistent"):
        reduce_paper_order(intent, (filled_without_acceptance,))

    accepted = PaperOrderEvent(
        intent_sha256=intent.sha256,
        sequence=1,
        event_type="ACCEPTED",
        occurred_at=intent.created_at + timedelta(milliseconds=10),
        cumulative_filled_quantity=0,
        adapter_event_id="accepted-1",
        adapter_evidence_sha256="8" * 64,
    )
    assert filled_without_acceptance.fill_evidence is not None
    above_limit = filled_without_acceptance.model_copy(
        update={
            "sequence": 2,
            "occurred_at": intent.created_at + timedelta(milliseconds=20),
            "average_fill_price": Decimal(101),
            "adapter_event_id": "above-limit",
            "fill_evidence": filled_without_acceptance.fill_evidence.model_copy(
                update={"fill_price": Decimal(101)}
            ),
        }
    )
    with pytest.raises(ValueError, match="violates"):
        reduce_paper_order(intent, (accepted, above_limit))


def test_filled_entry_plans_a_lineage_linked_sell_at_the_arbitration_horizon() -> None:
    policy, _, context, _ = _policy()
    request = _request()
    entry = plan_paper_order(request, *_policy()).intent
    assert entry is not None
    accepted = PaperOrderEvent(
        intent_sha256=entry.sha256,
        sequence=1,
        event_type="ACCEPTED",
        occurred_at=entry.created_at + timedelta(milliseconds=10),
        cumulative_filled_quantity=0,
        adapter_event_id="accepted-1",
        adapter_evidence_sha256="9" * 64,
    )
    filled = PaperOrderEvent(
        intent_sha256=entry.sha256,
        sequence=2,
        event_type="FILLED",
        occurred_at=entry.created_at + timedelta(milliseconds=20),
        cumulative_filled_quantity=100,
        average_fill_price=Decimal(100),
        adapter_event_id="fill-1",
        adapter_evidence_sha256="a" * 64,
        fill_evidence=PaperFillEvidence(
            symbol="VIC",
            action="BUY",
            observed_at=entry.created_at + timedelta(milliseconds=15),
            stream_session_id="session-1",
            receive_sequence=11,
            filled_quantity=100,
            available_quantity=100,
            fill_price=Decimal(100),
            source_sha256="b" * 64,
        ),
    )
    state = reduce_paper_order(entry, (accepted, filled))
    costs = request.costs
    reserved = reserve_paper_entry(request.resources, entry)
    resources = settle_paper_entry(reserved, entry, state, costs)
    observed_at = DECISION_AT + timedelta(seconds=301)
    market = _request().market.model_copy(
        update={
            "observed_at": observed_at,
            "quote_received_at": observed_at,
            "best_bid_price": Decimal(110),
            "best_ask_price": Decimal(111),
            "source_sha256": "f" * 64,
        }
    )

    plan = plan_paper_exit(
        PaperExitRequest(
            entry_intent=entry,
            entry_state=state,
            market=market,
            resources=resources,
        ),
        policy,
        context,
    )

    assert plan.status == "READY"
    assert plan.intent is not None
    assert plan.intent.leg == "EXIT"
    assert plan.intent.action == "SELL"
    assert plan.intent.quantity == 100
    assert plan.intent.limit_price == Decimal(110)
    assert plan.intent.parent_intent_sha256 == entry.sha256
    assert plan.intent.reserved_cash_vnd == 0
    assert "outcome" not in plan.model_dump_json().lower()
