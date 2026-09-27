"""Pure compare-and-swap transitions for paper cash and settled inventory."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from t0_trading.controls import AccountSnapshot, CostPolicy
from t0_trading.execution.model import (
    PaperOrderIntent,
    PaperOrderState,
    PaperReservation,
    PaperResourceLedger,
    PaperResourcePosition,
)

_BPS = Decimal(10_000)
_TERMINAL = {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}
_MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def initialize_paper_ledger(account: AccountSnapshot, trade_date: date) -> PaperResourceLedger:
    """Create the only valid revision-zero ledger from one account snapshot."""
    if account.as_of.astimezone(_MARKET_TIMEZONE).date() != trade_date:
        raise ValueError("paper ledger account snapshot must belong to its trade date")
    return PaperResourceLedger(
        trade_date=trade_date,
        account_snapshot_sha256=account.sha256,
        revision=0,
        available_cash_vnd=account.cash_vnd,
        positions=tuple(
            PaperResourcePosition(
                symbol=item.symbol,
                available_exit_quantity=item.settled_qty - item.core_min_qty,
            )
            for item in sorted(account.positions, key=lambda value: value.symbol)
        ),
        started_cycles=0,
        realized_net_pnl_vnd=Decimal(0),
        reservations=(),
    )


def _next(
    ledger: PaperResourceLedger,
    *,
    available_cash_vnd: Decimal | None = None,
    positions: tuple[PaperResourcePosition, ...] | None = None,
    started_cycles: int | None = None,
    realized_net_pnl_vnd: Decimal | None = None,
    reservations: tuple[PaperReservation, ...] | None = None,
) -> PaperResourceLedger:
    return PaperResourceLedger(
        trade_date=ledger.trade_date,
        account_snapshot_sha256=ledger.account_snapshot_sha256,
        revision=ledger.revision + 1,
        available_cash_vnd=(
            ledger.available_cash_vnd
            if available_cash_vnd is None
            else available_cash_vnd
        ),
        positions=ledger.positions if positions is None else positions,
        started_cycles=ledger.started_cycles if started_cycles is None else started_cycles,
        realized_net_pnl_vnd=(
            ledger.realized_net_pnl_vnd
            if realized_net_pnl_vnd is None
            else realized_net_pnl_vnd
        ),
        reservations=ledger.reservations if reservations is None else reservations,
    )


def _positions(
    ledger: PaperResourceLedger, symbol: str, delta: int
) -> tuple[PaperResourcePosition, ...]:
    found = False
    values: list[PaperResourcePosition] = []
    for item in ledger.positions:
        quantity = item.available_exit_quantity + delta if item.symbol == symbol else (
            item.available_exit_quantity
        )
        if item.symbol == symbol:
            found = True
        if quantity < 0:
            raise ValueError("paper reservation exceeds available settled inventory")
        values.append(PaperResourcePosition(symbol=item.symbol, available_exit_quantity=quantity))
    if not found:
        raise ValueError("paper reservation symbol is absent from the resource ledger")
    return tuple(values)


def reserve_paper_entry(
    ledger: PaperResourceLedger, intent: PaperOrderIntent
) -> PaperResourceLedger:
    """Reserve a READY entry against exactly the ledger hash used by its planner."""
    if (
        intent.leg != "ENTRY"
        or intent.trade_date != ledger.trade_date
        or intent.resource_ledger_sha256 != ledger.sha256
        or any(item.entry_intent_sha256 == intent.sha256 for item in ledger.reservations)
        or intent.reserved_cash_vnd > ledger.available_cash_vnd
    ):
        raise ValueError("paper entry cannot reserve the supplied ledger revision")
    reservations = tuple(
        sorted(
            (
                *ledger.reservations,
                PaperReservation(
                    entry_intent_sha256=intent.sha256,
                    symbol=intent.symbol,
                    status="ENTRY_PENDING",
                    reserved_cash_vnd=intent.reserved_cash_vnd,
                    reserved_exit_quantity=intent.reserved_exit_quantity,
                    entry_filled_quantity=0,
                ),
            ),
            key=lambda item: item.entry_intent_sha256,
        )
    )
    return _next(
        ledger,
        available_cash_vnd=ledger.available_cash_vnd - intent.reserved_cash_vnd,
        positions=_positions(ledger, intent.symbol, -intent.reserved_exit_quantity),
        started_cycles=ledger.started_cycles + 1,
        reservations=reservations,
    )


def settle_paper_entry(
    ledger: PaperResourceLedger,
    intent: PaperOrderIntent,
    state: PaperOrderState,
    costs: CostPolicy,
) -> PaperResourceLedger:
    """Release unused entry resources while retaining exit inventory for actual fills."""
    matches = [
        item
        for item in ledger.reservations
        if item.entry_intent_sha256 == intent.sha256 and item.status == "ENTRY_PENDING"
    ]
    if (
        intent.leg != "ENTRY"
        or state.intent_sha256 != intent.sha256
        or state.status not in _TERMINAL
        or len(matches) != 1
        or not costs.contains(intent.trade_date)
    ):
        raise ValueError("paper entry settlement does not match an active reservation")
    reservation = matches[0]
    filled = state.cumulative_filled_quantity
    average = state.average_fill_price
    spent = (
        Decimal(0)
        if average is None
        else average * filled * (1 + costs.buy_fee_bps / _BPS)
    )
    if spent > reservation.reserved_cash_vnd:
        raise ValueError("paper entry fill exceeds reserved cash")
    remaining = tuple(
        item for item in ledger.reservations if item.entry_intent_sha256 != intent.sha256
    )
    if filled:
        remaining = tuple(
            sorted(
                (
                    *remaining,
                    PaperReservation(
                        entry_intent_sha256=intent.sha256,
                        symbol=intent.symbol,
                        status="OPEN",
                        reserved_cash_vnd=Decimal(0),
                        reserved_exit_quantity=filled,
                        entry_filled_quantity=filled,
                        entry_average_fill_price=average,
                    ),
                ),
                key=lambda item: item.entry_intent_sha256,
            )
        )
    return _next(
        ledger,
        available_cash_vnd=(
            ledger.available_cash_vnd + reservation.reserved_cash_vnd - spent
        ),
        positions=_positions(
            ledger,
            intent.symbol,
            reservation.reserved_exit_quantity - filled,
        ),
        reservations=remaining,
    )


def reserve_paper_exit(
    ledger: PaperResourceLedger, intent: PaperOrderIntent
) -> PaperResourceLedger:
    """Bind one exit attempt to an open cycle at one exact ledger revision."""
    matches = [
        item
        for item in ledger.reservations
        if item.entry_intent_sha256 == intent.parent_intent_sha256 and item.status == "OPEN"
    ]
    if (
        intent.leg != "EXIT"
        or intent.resource_ledger_sha256 != ledger.sha256
        or len(matches) != 1
        or matches[0].reserved_exit_quantity != intent.quantity
    ):
        raise ValueError("paper exit cannot reserve the supplied open cycle")
    selected = matches[0]
    replacement = PaperReservation(
        entry_intent_sha256=selected.entry_intent_sha256,
        exit_intent_sha256=intent.sha256,
        symbol=selected.symbol,
        status="EXIT_PENDING",
        reserved_cash_vnd=selected.reserved_cash_vnd,
        reserved_exit_quantity=selected.reserved_exit_quantity,
        entry_filled_quantity=selected.entry_filled_quantity,
        entry_average_fill_price=selected.entry_average_fill_price,
    )
    return _next(
        ledger,
        reservations=tuple(
            replacement if item.entry_intent_sha256 == selected.entry_intent_sha256 else item
            for item in ledger.reservations
        ),
    )


def settle_paper_exit(
    ledger: PaperResourceLedger,
    intent: PaperOrderIntent,
    state: PaperOrderState,
    costs: CostPolicy,
) -> PaperResourceLedger:
    """Record realized paper PnL; retain an OPEN reservation after a partial exit."""
    matches = [
        item
        for item in ledger.reservations
        if item.exit_intent_sha256 == intent.sha256 and item.status == "EXIT_PENDING"
    ]
    if (
        intent.leg != "EXIT"
        or state.intent_sha256 != intent.sha256
        or state.status not in _TERMINAL
        or len(matches) != 1
        or not costs.contains(intent.trade_date)
    ):
        raise ValueError("paper exit settlement does not match an active reservation")
    reservation = matches[0]
    sold = state.cumulative_filled_quantity
    if sold > reservation.reserved_exit_quantity:
        raise ValueError("paper exit exceeds its reserved quantity")
    realized = Decimal(0)
    if sold:
        if state.average_fill_price is None or reservation.entry_average_fill_price is None:
            raise ValueError("paper exit settlement requires priced entry and exit fills")
        entry_cost = reservation.entry_average_fill_price * sold * (
            1 + costs.buy_fee_bps / _BPS
        )
        sale_value = state.average_fill_price * sold * (
            1 - (costs.sell_fee_bps + costs.sell_tax_bps) / _BPS
        )
        realized = sale_value - entry_cost
    outstanding = reservation.reserved_exit_quantity - sold
    remaining = tuple(
        item
        for item in ledger.reservations
        if item.entry_intent_sha256 != reservation.entry_intent_sha256
    )
    if outstanding:
        remaining = tuple(
            sorted(
                (
                    *remaining,
                    PaperReservation(
                        entry_intent_sha256=reservation.entry_intent_sha256,
                        symbol=reservation.symbol,
                        status="OPEN",
                        reserved_cash_vnd=Decimal(0),
                        reserved_exit_quantity=outstanding,
                        entry_filled_quantity=outstanding,
                        entry_average_fill_price=reservation.entry_average_fill_price,
                    ),
                ),
                key=lambda item: item.entry_intent_sha256,
            )
        )
    return _next(
        ledger,
        realized_net_pnl_vnd=ledger.realized_net_pnl_vnd + realized,
        reservations=remaining,
    )
