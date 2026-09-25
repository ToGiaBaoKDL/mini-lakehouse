"""Portfolio invariants and causal accounting for research-only T0 cycles."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError
from t0_trading.cli import app
from t0_trading.decisions import StrategyDecision
from t0_trading.market.session import MarketSession
from t0_trading.numeric import basis_points
from t0_trading.outcomes import OutcomeLabel
from t0_trading.simulation import (
    AccountPosition,
    AccountSnapshot,
    AdvancePolicy,
    ClosingMark,
    CycleProposal,
    RiskLimits,
    SelectionRecord,
    SimulationRequest,
    public_vndirect_dta_costs,
    simulate_cycles,
)
from typer.testing import CliRunner

TRADE_DATE = date(2026, 9, 4)
ACCOUNT_AT = datetime(2026, 9, 4, 1, tzinfo=UTC)
DECISION_AT = datetime(2026, 9, 4, 2, 30, tzinfo=UTC)
MARK_AT = datetime(2026, 9, 4, 8, tzinfo=UTC)


def _proposal(
    action: Literal["BUY", "SELL"] = "BUY",
    *,
    symbol: str = "VIC",
    decision_at: datetime = DECISION_AT,
    entry_price: str = "100",
    exit_price: str | None = "110",
    priority: int = 0,
) -> CycleProposal:
    score = Decimal("0.8") if action == "BUY" else Decimal("-0.8")
    decision = StrategyDecision(
        decision_version="decisions-v1",
        decision_configuration_sha256="d" * 64,
        strategy="momentum",
        strategy_version="scores-v1",
        strategy_configuration_sha256="b" * 64,
        outcome_version="outcomes-v1",
        outcome_configuration_sha256="c" * 64,
        feature_version="features-v1",
        feature_configuration_sha256="e" * 64,
        feature_snapshot_sha256="f" * 64 if symbol == "VIC" else "a" * 64,
        strategy_score_sha256="1" * 64,
        symbol=symbol,
        trade_date=TRADE_DATE,
        decision_at=decision_at,
        market_session=MarketSession.CONTINUOUS_AM,
        horizon_seconds=60,
        signed_score=score,
        minimum_strength=Decimal("0.5"),
        action=action,
        reasons=(),
    )
    entry = Decimal(entry_price)
    exit_value = Decimal(exit_price) if exit_price is not None else None
    gross = None
    if exit_value is not None:
        movement = exit_value - entry if action == "BUY" else entry - exit_value
        gross = basis_points(movement, entry)
    outcome = OutcomeLabel(
        outcome_version=decision.outcome_version,
        outcome_configuration_sha256=decision.outcome_configuration_sha256,
        feature_version=decision.feature_version,
        feature_configuration_sha256=decision.feature_configuration_sha256,
        feature_snapshot_sha256=decision.feature_snapshot_sha256,
        stream_session_id="session-1",
        symbol=symbol,
        trade_date=TRADE_DATE,
        decision_at=decision_at,
        action=action,
        horizon_seconds=60,
        order_quantity=100,
        entry_at=decision_at + timedelta(milliseconds=500),
        horizon_at=decision_at + timedelta(seconds=60),
        entry_quote_received_at=decision_at,
        entry_receive_sequence=1,
        entry_vwap=entry,
        horizon_quote_received_at=(
            decision_at + timedelta(seconds=60) if exit_value is not None else None
        ),
        horizon_receive_sequence=2 if exit_value is not None else None,
        horizon_vwap=exit_value,
        gross_return_bps=gross,
        reasons=() if exit_value is not None else ("MISSING_HORIZON_QUOTE",),
    )
    return CycleProposal(priority=priority, decision=decision, outcome=outcome)


def _request(
    proposals: tuple[CycleProposal, ...],
    *,
    cash: str = "20000",
    settled: int = 200,
    core: int = 100,
    verified: bool = False,
    risk: RiskLimits | None = None,
    lot_size: int = 100,
    advance: AdvancePolicy | None = None,
) -> SimulationRequest:
    costs = public_vndirect_dta_costs(TRADE_DATE, checked_at=datetime(2026, 9, 22, tzinfo=UTC))
    if not verified:
        costs = costs.model_copy(
            update={"fee_source": None, "fee_checked_at": None, "basis": "USER_SUPPLIED"}
        )
    return SimulationRequest(
        trade_date=TRADE_DATE,
        account=AccountSnapshot(
            as_of=ACCOUNT_AT,
            source="manual broker snapshot",
            cash_vnd=Decimal(cash),
            positions=(
                AccountPosition(
                    symbol="VIC",
                    settled_qty=settled,
                    t1_qty=0,
                    t2_qty=0,
                    core_min_qty=core,
                    start_price=Decimal(100),
                ),
            ),
        ),
        costs=costs,
        advance=advance,
        risk=risk
        or RiskLimits(
            max_cycle_quantity=100,
            max_order_notional_vnd=Decimal(20000),
            max_cycles_per_day=2,
            max_daily_loss_vnd=Decimal(1000),
            cash_reserve_vnd=Decimal(0),
        ),
        lot_size=lot_size,
        closing_marks=(
            ClosingMark(symbol="VIC", as_of=MARK_AT, price=Decimal(105), source="SSI daily"),
        ),
        selection_records=tuple(
            SelectionRecord(
                decision_sha256=proposal.decision.sha256,
                priority=proposal.priority,
                selected_at=proposal.decision.decision_at + timedelta(milliseconds=100),
                source="decision-only shadow journal",
                source_sha256="9" * 64,
            )
            for proposal in proposals
        ),
        proposals=proposals,
    )


def test_buy_low_sell_old_restores_total_inventory_and_reports_net_alpha() -> None:
    request = _request((_proposal(),))
    report = simulate_cycles(request)

    assert report.status == "COMPLETE"
    assert report.fee_provenance_recorded is False
    assert report.cycles[0].status == "CLOSED"
    assert report.cycles[0].gross_pnl_vnd == Decimal(1000)
    assert report.cycles[0].trading_cost_vnd == Decimal(32)
    assert report.net_t0_alpha_vnd == Decimal(968)
    assert report.hold_pnl_vnd == Decimal(1000)
    assert report.hold_plus_t0_pnl_vnd == Decimal(1968)
    assert report.ending_cash_vnd == Decimal(9990)
    assert report.pending_sale_proceeds_vnd == Decimal(10978)
    assert report.end_positions[0].settled_qty == 100
    assert report.end_positions[0].bought_today_qty == 100
    assert report.sha256 == simulate_cycles(request).sha256


def test_sell_high_buy_back_uses_settled_stock_and_requires_repurchase_cash() -> None:
    request = _request((_proposal("SELL", entry_price="110", exit_price="100"),))
    report = simulate_cycles(request)

    assert report.status == "COMPLETE"
    assert report.net_t0_alpha_vnd == Decimal(968)
    assert report.end_positions[0].settled_qty == 100
    assert report.end_positions[0].bought_today_qty == 100
    assert (
        simulate_cycles(_request((_proposal("SELL", entry_price="110", exit_price="400"),)))
        .cycles[0]
        .reason
        == "BUYBACK_CASH_SHORTFALL"
    )


def test_sale_receivable_does_not_fund_buyback_without_opted_in_advance() -> None:
    report = simulate_cycles(
        _request((_proposal("SELL", entry_price="110", exit_price="100"),), cash="0")
    )

    assert report.status == "INCOMPLETE"
    assert report.cycles[0].reason == "BUYBACK_CASH_SHORTFALL"
    assert report.ending_cash_vnd == 0
    assert report.pending_sale_proceeds_vnd == Decimal(10978)
    assert report.advance_principal_vnd == 0


def test_advance_is_explicit_and_financing_is_deducted_from_alpha() -> None:
    advance = AdvancePolicy(
        settlement_date=date(2026, 9, 8),
        daily_interest_bps=Decimal("3.7"),
        source="VNDIRECT public UTTB scenario; opt-in assumed",
    )
    report = simulate_cycles(
        _request(
            (_proposal("SELL", entry_price="110", exit_price="100"),),
            cash="0",
            advance=advance,
        )
    )

    assert report.status == "COMPLETE"
    assert report.pending_sale_proceeds_vnd == Decimal(10978)
    assert report.advance_principal_vnd == Decimal(10010)
    assert report.advance_interest_vnd == Decimal("14.8148")
    assert report.financing_cost_vnd == Decimal("14.8148")
    assert report.net_t0_alpha_vnd == Decimal("953.1852")


def test_core_and_cash_gates_reject_without_creating_a_trade() -> None:
    proposal = _proposal()
    for request, reason in (
        (_request((proposal,), settled=100), "INSUFFICIENT_SETTLED_ABOVE_CORE"),
        (_request((proposal,), cash="10000"), "CASH_LIMIT"),
        (_request((proposal,), lot_size=200), "LOT_SIZE"),
    ):
        report = simulate_cycles(request)
        assert report.status == "COMPLETE"
        assert report.cycles[0].status == "REJECTED"
        assert report.cycles[0].reason == reason
        assert report.net_t0_alpha_vnd == 0
        assert report.ending_cash_vnd == request.account.cash_vnd


def test_missing_future_book_leaves_cycle_open_not_a_fictitious_profit() -> None:
    report = simulate_cycles(_request((_proposal(exit_price=None),)))

    assert report.status == "INCOMPLETE"
    assert report.cycles[0].status == "OPEN"
    assert report.cycles[0].reason == "EXIT_BOOK_UNAVAILABLE"
    assert report.net_t0_alpha_vnd is None
    assert report.hold_plus_t0_pnl_vnd is None


def test_selected_candidates_cannot_silently_arbitrate_a_same_symbol_clock() -> None:
    buy = _proposal("BUY")
    sell = _proposal("SELL", priority=1)
    with pytest.raises(ValidationError, match="unique decisions and symbol clocks"):
        _request((buy, sell))


def test_exact_lineage_and_account_snapshot_are_required() -> None:
    proposal = _proposal()
    changed = proposal.outcome.model_copy(update={"feature_snapshot_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="lineage do not match"):
        CycleProposal(priority=0, decision=proposal.decision, outcome=changed)
    with pytest.raises(ValidationError, match="timestamps must be timezone-aware"):
        AccountSnapshot(
            as_of=datetime(2026, 9, 4, 8),
            source="manual",
            cash_vnd=Decimal(100),
            positions=_request(()).account.positions,
        )


def test_selection_must_be_recorded_before_entry_and_match_priority() -> None:
    request = _request((_proposal(),))
    payload = request.model_dump(mode="json")
    payload["selection_records"][0]["selected_at"] = request.proposals[
        0
    ].outcome.entry_at.isoformat()
    with pytest.raises(ValidationError, match="selection must match priority"):
        SimulationRequest.model_validate(payload)


def test_ineligible_priced_outcome_cannot_be_paired_with_actionable_decision() -> None:
    proposal = _proposal()
    with pytest.raises(ValidationError, match="ineligible priced outcome"):
        CycleProposal(
            priority=0,
            decision=proposal.decision,
            outcome=proposal.outcome.model_copy(update={"reasons": ("FEATURE_INELIGIBLE",)}),
        )


def test_verified_fee_provenance_is_distinct_from_research_approval() -> None:
    report = simulate_cycles(_request((_proposal(),), verified=True))
    assert report.fee_provenance_recorded is True
    assert report.fee_basis == "PUBLIC_SCHEDULE_ASSUMPTION"
    assert report.account_plan == "VNDIRECT_DTA"
    assert report.status == "COMPLETE"
    assert "PASS" not in report.model_dump_json()


def test_bought_today_inventory_cannot_fund_another_t0_sale() -> None:
    first = _proposal()
    second = _proposal(decision_at=DECISION_AT + timedelta(minutes=2))
    report = simulate_cycles(_request((first, second)))

    assert [cycle.status for cycle in report.cycles] == ["CLOSED", "REJECTED"]
    assert report.cycles[1].reason == "INSUFFICIENT_SETTLED_ABOVE_CORE"
    assert report.net_t0_alpha_vnd == Decimal(968)


def test_realized_daily_loss_vetoes_later_cycle() -> None:
    first = _proposal(exit_price="90")
    second = _proposal(decision_at=DECISION_AT + timedelta(minutes=2))
    report = simulate_cycles(_request((first, second)))

    assert report.cycles[0].net_pnl_vnd == Decimal(-1028)
    assert report.cycles[1].reason == "DAILY_LOSS_LIMIT"


def test_explicit_priority_determines_cash_allocation_at_same_instant() -> None:
    base = _request(())
    account = base.account.model_copy(
        update={
            "cash_vnd": Decimal("10010"),
            "positions": (
                *base.account.positions,
                base.account.positions[0].model_copy(update={"symbol": "VHM"}),
            ),
        }
    )
    request = SimulationRequest(
        trade_date=TRADE_DATE,
        account=account,
        costs=base.costs,
        risk=base.risk,
        lot_size=100,
        closing_marks=(
            *base.closing_marks,
            base.closing_marks[0].model_copy(update={"symbol": "VHM"}),
        ),
        selection_records=tuple(
            SelectionRecord(
                decision_sha256=proposal.decision.sha256,
                priority=proposal.priority,
                selected_at=proposal.decision.decision_at + timedelta(milliseconds=100),
                source="decision-only shadow journal",
                source_sha256="9" * 64,
            )
            for proposal in (
                _proposal(symbol="VIC", priority=1),
                _proposal(symbol="VHM", priority=0),
            )
        ),
        proposals=(_proposal(symbol="VIC", priority=1), _proposal(symbol="VHM", priority=0)),
    )

    report = simulate_cycles(request)

    assert [(cycle.symbol, cycle.status) for cycle in report.cycles] == [
        ("VHM", "CLOSED"),
        ("VIC", "REJECTED"),
    ]
    assert report.cycles[1].reason == "CASH_LIMIT"


def test_offline_cli_emits_complete_report_and_nonzero_for_open_cycle(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "report.json"
    request_path.write_text(_request((_proposal(),)).model_dump_json(), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["simulate-cycles", "--input", str(request_path), "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    assert '"net_t0_alpha_vnd": "968"' in output_path.read_text(encoding="utf-8")

    request_path.write_text(
        _request((_proposal(exit_price=None),)).model_dump_json(), encoding="utf-8"
    )
    incomplete = CliRunner().invoke(app, ["simulate-cycles", "--input", str(request_path)])
    assert incomplete.exit_code == 10
    assert '"status": "INCOMPLETE"' in incomplete.stdout
