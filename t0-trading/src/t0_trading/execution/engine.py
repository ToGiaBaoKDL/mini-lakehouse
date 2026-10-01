"""Pure paper-order planner and append-only lifecycle reducer."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from t0_trading.configuration import (
    CandidateArbitrationVersion,
    ContextVersion,
    PaperExecutionVersion,
    PromotionGateVersion,
)
from t0_trading.execution.model import (
    PaperBlockReason,
    PaperExecutionRequest,
    PaperExitRequest,
    PaperOrderEvent,
    PaperOrderIntent,
    PaperOrderPlan,
    PaperOrderState,
)

_BPS = Decimal(10_000)
_TERMINAL = {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}


def plan_paper_order(
    request: PaperExecutionRequest,
    policy: PaperExecutionVersion,
    arbitration_policy: CandidateArbitrationVersion,
    context_policy: ContextVersion,
    promotion_policy: PromotionGateVersion,
) -> PaperOrderPlan:
    """Plan a paper-only BUY limit order without consulting future outcomes."""
    candidate = request.candidate
    arbitration = request.arbitration
    market = request.market
    if (
        not policy.contains(candidate.trade_date)
        or not context_policy.contains(candidate.trade_date)
        or policy.arbitration_version != arbitration_policy.version
        or policy.promotion_gate_version != promotion_policy.version
        or policy.context_version != context_policy.version
        or arbitration.arbitration_version != arbitration_policy.version
        or arbitration.arbitration_configuration_sha256 != arbitration_policy.sha256
        or request.promotion.gate_version != promotion_policy.version
        or request.promotion.gate_configuration_sha256 != promotion_policy.sha256
        or request.promotion.evaluation_version != promotion_policy.evaluation_version
        or request.promotion.arbitration_version != promotion_policy.arbitration_version
    ):
        raise ValueError("paper execution policy does not match selected arbitration")
    quantity = policy.order_quantity
    limit_price = market.best_ask_price + market.tick_size * policy.limit_offset_ticks
    notional = limit_price * quantity
    reserved_cash = notional * (1 + request.costs.buy_fee_bps / _BPS)
    resource_position = next(
        (item for item in request.resources.positions if item.symbol == candidate.symbol),
        None,
    )
    reasons: list[PaperBlockReason] = []
    if market.market_status not in context_policy.tradable_market_statuses:
        reasons.append("MARKET_STATUS_INELIGIBLE")
    if (market.observed_at - market.quote_received_at).total_seconds() > (
        policy.maximum_quote_age_seconds
    ):
        reasons.append("STALE_QUOTE")
    if market.best_ask_quantity < quantity:
        reasons.append("INSUFFICIENT_ASK_QUANTITY")
    if limit_price > market.ceiling_price or limit_price < market.floor_price:
        reasons.append("PRICE_LIMIT")
    if resource_position is None:
        reasons.append("POSITION_UNAVAILABLE")
    elif resource_position.available_exit_quantity < quantity:
        reasons.append("INSUFFICIENT_SETTLED_ABOVE_CORE")
    if quantity > request.risk.max_cycle_quantity:
        reasons.append("QUANTITY_LIMIT")
    if notional > request.risk.max_order_notional_vnd:
        reasons.append("ORDER_NOTIONAL_LIMIT")
    if request.resources.started_cycles >= request.risk.max_cycles_per_day:
        reasons.append("CYCLE_LIMIT")
    if request.resources.realized_net_pnl_vnd <= -request.risk.max_daily_loss_vnd:
        reasons.append("DAILY_LOSS_LIMIT")
    if request.resources.available_cash_vnd - reserved_cash < request.risk.cash_reserve_vnd:
        reasons.append("CASH_LIMIT")
    if reasons:
        return PaperOrderPlan(
            arbitration_sha256=arbitration.sha256,
            status="BLOCKED",
            reasons=tuple(reasons),
            intent=None,
        )
    if arbitration.priority is None:
        raise ValueError("selected arbitration is missing paper execution priority")
    return PaperOrderPlan(
        arbitration_sha256=arbitration.sha256,
        status="READY",
        reasons=(),
        intent=PaperOrderIntent(
            execution_version=policy.version,
            execution_configuration_sha256=policy.sha256,
            request_sha256=request.sha256,
            promotion_report_sha256=request.promotion.sha256,
            arbitration_sha256=arbitration.sha256,
            candidate_sha256=candidate.sha256,
            strategy=candidate.strategy,
            symbol=candidate.symbol,
            trade_date=candidate.trade_date,
            decision_at=candidate.decision_at,
            horizon_seconds=arbitration.horizon_seconds,
            leg="ENTRY",
            action="BUY",
            quantity=quantity,
            limit_price=limit_price,
            priority=arbitration.priority,
            created_at=market.observed_at,
            expires_at=market.observed_at + timedelta(seconds=policy.time_in_force_seconds),
            reserved_cash_vnd=reserved_cash,
            reserved_exit_quantity=quantity,
            market_snapshot_sha256=market.sha256,
            selection_source_sha256=request.selection.source_sha256,
            resource_ledger_sha256=request.resources.sha256,
        ),
    )


def plan_paper_exit(
    request: PaperExitRequest,
    policy: PaperExecutionVersion,
    context_policy: ContextVersion,
) -> PaperOrderPlan:
    """Plan the SELL leg from terminal paper entry evidence, never from an outcome label."""
    entry = request.entry_intent
    state = request.entry_state
    market = request.market
    reservation = next(
        item
        for item in request.resources.reservations
        if item.entry_intent_sha256 == entry.sha256 and item.status == "OPEN"
    )
    if (
        not policy.contains(entry.trade_date)
        or not context_policy.contains(entry.trade_date)
        or entry.execution_version != policy.version
        or entry.execution_configuration_sha256 != policy.sha256
    ):
        raise ValueError("paper exit policy does not match its entry intent")
    quantity = reservation.reserved_exit_quantity
    limit_price = market.best_bid_price - market.tick_size * policy.limit_offset_ticks
    reasons: list[PaperBlockReason] = []
    if state.status not in _TERMINAL:
        reasons.append("ENTRY_NOT_TERMINAL")
    if quantity == 0:
        reasons.append("ENTRY_UNFILLED")
    if market.observed_at < entry.decision_at + timedelta(seconds=entry.horizon_seconds):
        reasons.append("BEFORE_EXIT_HORIZON")
    if market.market_status not in context_policy.tradable_market_statuses:
        reasons.append("MARKET_STATUS_INELIGIBLE")
    if (market.observed_at - market.quote_received_at).total_seconds() > (
        policy.maximum_quote_age_seconds
    ):
        reasons.append("STALE_QUOTE")
    if market.best_bid_quantity < quantity:
        reasons.append("INSUFFICIENT_BID_QUANTITY")
    if limit_price < market.floor_price or limit_price > market.ceiling_price:
        reasons.append("PRICE_LIMIT")
    if reasons:
        return PaperOrderPlan(
            arbitration_sha256=entry.arbitration_sha256,
            status="BLOCKED",
            reasons=tuple(reasons),
            intent=None,
        )
    return PaperOrderPlan(
        arbitration_sha256=entry.arbitration_sha256,
        status="READY",
        reasons=(),
        intent=PaperOrderIntent(
            execution_version=entry.execution_version,
            execution_configuration_sha256=entry.execution_configuration_sha256,
            request_sha256=request.sha256,
            promotion_report_sha256=entry.promotion_report_sha256,
            arbitration_sha256=entry.arbitration_sha256,
            candidate_sha256=entry.candidate_sha256,
            strategy=entry.strategy,
            symbol=entry.symbol,
            trade_date=entry.trade_date,
            decision_at=entry.decision_at,
            horizon_seconds=entry.horizon_seconds,
            leg="EXIT",
            action="SELL",
            quantity=quantity,
            limit_price=limit_price,
            priority=entry.priority,
            created_at=market.observed_at,
            expires_at=market.observed_at + timedelta(seconds=policy.time_in_force_seconds),
            reserved_cash_vnd=Decimal(0),
            reserved_exit_quantity=quantity,
            market_snapshot_sha256=market.sha256,
            selection_source_sha256=entry.selection_source_sha256,
            resource_ledger_sha256=request.resources.sha256,
            parent_intent_sha256=entry.sha256,
        ),
    )


def reduce_paper_order(
    intent: PaperOrderIntent,
    events: Sequence[PaperOrderEvent],
) -> PaperOrderState:
    """Reduce ordered adapter evidence while rejecting impossible order histories."""
    status = "CREATED"
    filled = 0
    average = None
    last_at = intent.created_at
    last_sequence = 0
    hashes: set[str] = set()
    adapter_ids: set[str] = set()
    fill_positions: set[tuple[str, int, str]] = set()
    last_fill_position: tuple[datetime, str, int] | None = None
    allowed = {
        "CREATED": {"ACCEPTED", "REJECTED", "EXPIRED"},
        "ACCEPTED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED"},
        "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED"},
    }
    for event in events:
        if (
            event.intent_sha256 != intent.sha256
            or event.sha256 in hashes
            or event.adapter_event_id in adapter_ids
            or event.sequence <= last_sequence
            or event.occurred_at < last_at
            or status in _TERMINAL
            or event.event_type not in allowed[status]
            or event.cumulative_filled_quantity < filled
            or event.cumulative_filled_quantity > intent.quantity
        ):
            raise ValueError("paper order event history is inconsistent")
        if event.event_type == "ACCEPTED" and event.cumulative_filled_quantity != 0:
            raise ValueError("accepted paper order cannot already contain a fill")
        if event.event_type == "REJECTED" and event.cumulative_filled_quantity != 0:
            raise ValueError("rejected paper order cannot contain a fill")
        if event.event_type == "PARTIALLY_FILLED" and not (
            filled < event.cumulative_filled_quantity < intent.quantity
        ):
            raise ValueError("partial paper fill quantity is inconsistent")
        if event.event_type == "FILLED" and event.cumulative_filled_quantity != intent.quantity:
            raise ValueError("filled paper order must reach its complete quantity")
        if event.cumulative_filled_quantity == filled and event.average_fill_price != average:
            raise ValueError("paper fill average changed without additional quantity")
        increment = event.cumulative_filled_quantity - filled
        evidence = event.fill_evidence
        if (increment > 0) != (evidence is not None):
            raise ValueError("paper quantity changes require fill evidence")
        if evidence is not None:
            fill_position = (
                evidence.observed_at,
                evidence.stream_session_id,
                evidence.receive_sequence,
            )
            fill_identity = (
                evidence.stream_session_id,
                evidence.receive_sequence,
                evidence.source_sha256,
            )
            if filled == 0:
                expected_average = evidence.fill_price
            else:
                if average is None:
                    raise ValueError("existing paper fill is missing its average price")
                expected_average = (
                    average * filled + evidence.fill_price * increment
                ) / event.cumulative_filled_quantity
            if (
                evidence.symbol != intent.symbol
                or evidence.action != intent.action
                or evidence.filled_quantity != increment
                or evidence.observed_at < intent.created_at
                or evidence.observed_at > event.occurred_at
                or event.average_fill_price != expected_average
                or fill_identity in fill_positions
                or (last_fill_position is not None and fill_position <= last_fill_position)
            ):
                raise ValueError("paper fill evidence does not reconcile with the order event")
            if (intent.action == "BUY" and evidence.fill_price > intent.limit_price) or (
                intent.action == "SELL" and evidence.fill_price < intent.limit_price
            ):
                raise ValueError("paper fill violates its limit price")
            fill_positions.add(fill_identity)
            last_fill_position = fill_position
        if event.average_fill_price is not None and (
            (intent.action == "BUY" and event.average_fill_price > intent.limit_price)
            or (intent.action == "SELL" and event.average_fill_price < intent.limit_price)
        ):
            raise ValueError("paper fill violates its limit price")
        if event.event_type in {"ACCEPTED", "PARTIALLY_FILLED", "FILLED"} and (
            event.occurred_at > intent.expires_at
        ):
            raise ValueError("paper order activity cannot occur after expiry")
        if event.event_type == "EXPIRED" and event.occurred_at < intent.expires_at:
            raise ValueError("paper order cannot expire before its deadline")
        hashes.add(event.sha256)
        adapter_ids.add(event.adapter_event_id)
        status = event.event_type
        filled = event.cumulative_filled_quantity
        average = event.average_fill_price
        last_at = event.occurred_at
        last_sequence = event.sequence
    return PaperOrderState(
        intent_sha256=intent.sha256,
        status=status,
        cumulative_filled_quantity=filled,
        average_fill_price=average,
        event_count=len(events),
        last_event_sha256=events[-1].sha256 if events else None,
    )
