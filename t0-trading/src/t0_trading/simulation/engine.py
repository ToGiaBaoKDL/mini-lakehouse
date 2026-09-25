"""Causal, side-effect-free accounting for selected manual T0 research cycles."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from t0_trading.simulation.model import (
    CycleProposal,
    CycleResult,
    EndPosition,
    SimulationReport,
    SimulationRequest,
)

_BPS = Decimal(10_000)


@dataclass(slots=True)
class _Position:
    settled: int
    t1: int
    t2: int
    bought_today: int = 0

    @property
    def total(self) -> int:
        return self.settled + self.t1 + self.t2 + self.bought_today


@dataclass(frozen=True, slots=True)
class _Execution:
    price: Decimal
    notional: Decimal
    charge: Decimal


@dataclass(frozen=True, slots=True)
class _OpenCycle:
    proposal: CycleProposal
    entry: _Execution


def _execution(
    request: SimulationRequest, book_price: Decimal, action: str, quantity: int
) -> _Execution:
    costs = request.costs
    slip = costs.extra_slippage_bps / _BPS
    price = book_price * (1 + slip if action == "BUY" else 1 - slip)
    notional = price * quantity
    fee_bps = costs.buy_fee_bps if action == "BUY" else costs.sell_fee_bps
    tax_bps = costs.sell_tax_bps if action == "SELL" else Decimal(0)
    return _Execution(price, notional, notional * (fee_bps + tax_bps) / _BPS)


def _result(
    proposal: CycleProposal,
    *,
    status: Literal["CLOSED", "REJECTED", "OPEN"],
    reason: str | None = None,
    entry: _Execution | None = None,
    exit: _Execution | None = None,
) -> CycleResult:
    decision, outcome = proposal.decision, proposal.outcome
    if decision.action == "ABSTAIN":
        raise ValueError("cannot simulate an abstention")
    gross = None
    charge = None
    net = None
    if status == "CLOSED":
        if entry is None or exit is None:
            raise ValueError("closed cycle requires both executions")
        gross = (
            exit.notional - entry.notional
            if decision.action == "BUY"
            else entry.notional - exit.notional
        )
        charge = entry.charge + exit.charge
        net = gross - charge
    return CycleResult(
        cycle_id=decision.sha256,
        outcome_sha256=outcome.sha256,
        symbol=decision.symbol,
        strategy=decision.strategy,
        action=decision.action,
        quantity=outcome.order_quantity,
        entry_at=outcome.entry_at,
        horizon_at=outcome.horizon_at,
        status=status,
        reason=reason,
        entry_price=entry.price if entry is not None else None,
        exit_price=exit.price if exit is not None else None,
        gross_pnl_vnd=gross,
        trading_cost_vnd=charge,
        net_pnl_vnd=net,
    )


def _fund_buy(
    request: SimulationRequest,
    *,
    cash: Decimal,
    pending_sales: Decimal,
    advance_principal: Decimal,
    advance_interest: Decimal,
    spend: Decimal,
) -> tuple[Decimal, Decimal] | None:
    """Return a permitted advance draw and its full-to-settlement interest."""
    draw = max(spend + request.risk.cash_reserve_vnd - cash, Decimal(0))
    if draw == 0:
        return Decimal(0), Decimal(0)
    policy = request.advance
    if policy is None:
        return None
    days = (policy.settlement_date - request.trade_date).days
    interest = draw * policy.daily_interest_bps * days / _BPS
    if advance_principal + draw + advance_interest + interest > pending_sales:
        return None
    return draw, interest


def simulate_cycles(request: SimulationRequest) -> SimulationReport:
    """Replay explicitly selected cycles; future exit prices never select an entry.

    BUY first consumes cash and later sells previously settled stock. SELL first
    consumes settled stock and later buys unsettled replacement stock. Rejected
    entries do not trade; an unavailable exit leaves the portfolio open and makes
    aggregate Net T0 Alpha unavailable rather than inventing a fill.
    """
    initial = {item.symbol: item for item in request.account.positions}
    positions = {
        symbol: _Position(item.settled_qty, item.t1_qty, item.t2_qty)
        for symbol, item in initial.items()
    }
    cash = request.account.cash_vnd
    pending_sales = Decimal(0)
    advance_principal = Decimal(0)
    advance_interest = Decimal(0)
    open_by_symbol: dict[str, _OpenCycle] = {}
    results: dict[str, CycleResult] = {}
    started = 0
    realized_net = Decimal(0)

    # Exit-before-entry at an identical instant permits one completed cycle to
    # release its symbol and cash without allowing any earlier decision to see it.
    events = sorted(
        (
            (time, phase, proposal.priority, proposal)
            for proposal in request.proposals
            for time, phase in ((proposal.outcome.entry_at, 1), (proposal.outcome.horizon_at, 0))
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    for _, phase, _, proposal in events:
        decision, outcome = proposal.decision, proposal.outcome
        cycle_id = decision.sha256
        position = positions[decision.symbol]
        quantity = outcome.order_quantity
        if phase == 1:
            reason = None
            if decision.symbol in open_by_symbol:
                reason = "SYMBOL_BUSY"
            elif outcome.entry_vwap is None:
                reason = "ENTRY_BOOK_UNAVAILABLE"
            elif quantity % request.lot_size:
                reason = "LOT_SIZE"
            elif quantity > request.risk.max_cycle_quantity:
                reason = "QUANTITY_LIMIT"
            elif started >= request.risk.max_cycles_per_day:
                reason = "CYCLE_LIMIT"
            elif realized_net <= -request.risk.max_daily_loss_vnd:
                reason = "DAILY_LOSS_LIMIT"
            elif position.settled - quantity < initial[decision.symbol].core_min_qty:
                reason = "INSUFFICIENT_SETTLED_ABOVE_CORE"
            if reason is not None:
                results[cycle_id] = _result(proposal, status="REJECTED", reason=reason)
                continue

            if outcome.entry_vwap is None or decision.action == "ABSTAIN":
                raise ValueError("actionable entry requires a book price and direction")
            entry = _execution(request, outcome.entry_vwap, decision.action, quantity)
            if entry.notional > request.risk.max_order_notional_vnd:
                results[cycle_id] = _result(
                    proposal, status="REJECTED", reason="ORDER_NOTIONAL_LIMIT"
                )
                continue
            funding = (
                _fund_buy(
                    request,
                    cash=cash,
                    pending_sales=pending_sales,
                    advance_principal=advance_principal,
                    advance_interest=advance_interest,
                    spend=entry.notional + entry.charge,
                )
                if decision.action == "BUY"
                else (Decimal(0), Decimal(0))
            )
            if funding is None:
                results[cycle_id] = _result(proposal, status="REJECTED", reason="CASH_LIMIT")
                continue

            if decision.action == "BUY":
                draw, interest = funding
                cash += draw
                advance_principal += draw
                advance_interest += interest
                realized_net -= interest
                cash -= entry.notional + entry.charge
                position.bought_today += quantity
            else:
                pending_sales += entry.notional - entry.charge
                position.settled -= quantity
            open_by_symbol[decision.symbol] = _OpenCycle(proposal, entry)
            started += 1
            continue

        opened = open_by_symbol.get(decision.symbol)
        if opened is None or opened.proposal.decision.sha256 != cycle_id:
            continue  # This candidate was rejected at entry, or another cycle owns the symbol.
        if outcome.horizon_vwap is None:
            results[cycle_id] = _result(
                proposal, status="OPEN", reason="EXIT_BOOK_UNAVAILABLE", entry=opened.entry
            )
            continue
        exit_action = "SELL" if decision.action == "BUY" else "BUY"
        exit_execution = _execution(request, outcome.horizon_vwap, exit_action, quantity)
        funding = (
            _fund_buy(
                request,
                cash=cash,
                pending_sales=pending_sales,
                advance_principal=advance_principal,
                advance_interest=advance_interest,
                spend=exit_execution.notional + exit_execution.charge,
            )
            if exit_action == "BUY"
            else (Decimal(0), Decimal(0))
        )
        if funding is None:
            results[cycle_id] = _result(
                proposal, status="OPEN", reason="BUYBACK_CASH_SHORTFALL", entry=opened.entry
            )
            continue
        if exit_action == "SELL":
            if position.settled - quantity < initial[decision.symbol].core_min_qty:
                results[cycle_id] = _result(
                    proposal, status="OPEN", reason="EXIT_SETTLED_SHORTFALL", entry=opened.entry
                )
                continue
            position.settled -= quantity
            pending_sales += exit_execution.notional - exit_execution.charge
        else:
            draw, interest = funding
            cash += draw
            advance_principal += draw
            advance_interest += interest
            realized_net -= interest
            position.bought_today += quantity
            cash -= exit_execution.notional + exit_execution.charge
        result = _result(proposal, status="CLOSED", entry=opened.entry, exit=exit_execution)
        results[cycle_id] = result
        if result.net_pnl_vnd is None:
            raise ValueError("closed cycle is missing net PnL")
        realized_net += result.net_pnl_vnd
        del open_by_symbol[decision.symbol]

    ordered = tuple(
        results[item.decision.sha256]
        for item in sorted(
            request.proposals,
            key=lambda value: (value.outcome.entry_at, value.priority),
        )
    )
    complete = not open_by_symbol
    gross_alpha = sum(
        (item.gross_pnl_vnd for item in ordered if item.gross_pnl_vnd is not None),
        Decimal(0),
    )
    trading_cost = sum(
        (item.trading_cost_vnd for item in ordered if item.trading_cost_vnd is not None),
        Decimal(0),
    )
    closing_prices = {mark.symbol: mark.price for mark in request.closing_marks}
    hold_pnl = sum(
        (
            item.total_qty * (closing_prices[item.symbol] - item.start_price)
            for item in request.account.positions
        ),
        Decimal(0),
    )
    if complete:
        if any(positions[symbol].total != item.total_qty for symbol, item in initial.items()):
            raise ValueError("closed T0 cycles must restore each symbol's total inventory")
        if (
            cash + pending_sales - advance_principal - advance_interest - request.account.cash_vnd
            != gross_alpha - trading_cost - advance_interest
        ):
            raise ValueError("T0 cash movement does not reconcile with cycle net PnL")
    return SimulationReport(
        trade_date=request.trade_date,
        request_sha256=request.sha256,
        fee_provenance_recorded=request.costs.fee_checked_at is not None,
        fee_basis=request.costs.basis,
        account_plan=request.costs.account_plan,
        selection_reference_recorded=bool(request.selection_records)
        and all(item.source_sha256 for item in request.selection_records),
        status="COMPLETE" if complete else "INCOMPLETE",
        cycles=ordered,
        starting_cash_vnd=request.account.cash_vnd,
        ending_cash_vnd=cash,
        pending_sale_proceeds_vnd=pending_sales,
        advance_principal_vnd=advance_principal,
        advance_interest_vnd=advance_interest,
        end_positions=tuple(
            EndPosition(
                symbol=symbol,
                settled_qty=value.settled,
                t1_qty=value.t1,
                t2_qty=value.t2,
                bought_today_qty=value.bought_today,
            )
            for symbol, value in sorted(positions.items())
        ),
        hold_pnl_vnd=hold_pnl,
        gross_t0_alpha_vnd=gross_alpha if complete else None,
        trading_cost_vnd=trading_cost if complete else None,
        financing_cost_vnd=advance_interest if complete else None,
        net_t0_alpha_vnd=gross_alpha - trading_cost - advance_interest if complete else None,
        hold_plus_t0_pnl_vnd=(
            hold_pnl + gross_alpha - trading_cost - advance_interest if complete else None
        ),
    )
