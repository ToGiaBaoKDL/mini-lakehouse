"""Paper execution is causal, fail-closed, and broker neutral."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

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
from t0_trading.promotion import PromotionGateReport, PromotionTargetResult
from t0_trading.strategy.baselines import (
    BASELINE_GROUP_NAMES,
    BaselineCandidate,
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
                strategy=target.strategy,
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
        groups=tuple(
            GroupEvidence(name=name, strength=Decimal("0.8"))
            for name in BASELINE_GROUP_NAMES["momentum_pullback"]
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
